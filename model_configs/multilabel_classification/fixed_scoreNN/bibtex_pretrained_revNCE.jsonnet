local test = std.extVar('TEST');  // a test run with small dataset
local data_dir = std.extVar('DATA_DIR');
local cuda_device = std.extVar('CUDA_DEVICE');
local use_wandb = (if test == '1' then false else true);

local dataset_name = 'bibtex_original';
local dataset_metadata = (import '../../datasets.jsonnet')[dataset_name];
local num_labels = dataset_metadata.num_labels;
local num_input_features = dataset_metadata.input_features;

// model variables
local ff_hidden = std.parseJson(std.extVar('ff_hidden'));
local label_space_dim = ff_hidden;
local ff_dropout = std.parseJson(std.extVar('ff_dropout'));
//local ff_activation = std.parseJson(std.extVar('ff_activation'));
local ff_activation = 'softplus';
local ff_linear_layers = std.parseJson(std.extVar('ff_linear_layers'));
local ff_weight_decay = std.parseJson(std.extVar('ff_weight_decay'));
//local global_score_hidden_dim = 150;
local global_score_hidden_dim = std.parseJson(std.extVar('global_score_hidden_dim'));
local cross_entropy_loss_weight = std.parseJson(std.extVar('cross_entropy_loss_weight'));
local inference_score_weight = std.parseJson(std.extVar('inference_score_weight'));
local oracle_cost_weight = 1; #std.parseJson(std.extVar('oracle_cost_weight'));
local gain = (if ff_activation == 'tanh' then 5 / 3 else 1);
local sc_temp = std.parseJson(std.extVar('stopping_criteria'));
local stopping_criteria = (if sc_temp == 0 then 1 else sc_temp);

{
  [if use_wandb then 'type']: 'train_test_log_to_wandb',
  evaluate_on_test: true,
  // Data
  dataset_reader: {
    type: 'arff',
    num_labels: num_labels,
  },
  validation_dataset_reader: {
    type: 'arff',
    num_labels: num_labels,
  },
  train_data_path: (data_dir + '/' + dataset_metadata.dir_name + '/' +
                    dataset_metadata.train_file),
  validation_data_path: (data_dir + '/' + dataset_metadata.dir_name + '/' +
                         dataset_metadata.validation_file),
  test_data_path: (data_dir + '/' + dataset_metadata.dir_name + '/' +
                   dataset_metadata.test_file),

  // Model
  model: {
    type: 'multi-label-classification-with-infnet',
    sampler: {
      type: 'appending-container',
      log_key: 'sampler',
      constituent_samplers: [],
    },
    inference_module: {
      type: 'inference-network-unnormalized',
      log_key: "tasknn",
      optimizer: {
        lr: 0.0002,
        weight_decay: ff_weight_decay,
        type: 'adam',
      },
      loss_fn: {
        type: 'combination-loss',
        log_key: 'loss',
        constituent_losses: [
          {
            type: 'multi-label-score-loss',
            log_key: 'infscore-loss',
            reduction: 'none',
            normalize_y: true,
          },  //This loss can be different from the main loss // change this
          {
            type: 'multi-label-bce',
            log_key: 'sampler-bce-loss',
            reduction: 'none',
          },
        ],
        loss_weights: [inference_score_weight, cross_entropy_loss_weight],
        reduction: 'mean',
      },
      stopping_criteria: stopping_criteria,
    },
    task_nn: {
        type: 'multi-label-classification',
        feature_network: {
          input_dim: num_input_features,
          num_layers: ff_linear_layers,
          activations: ([ff_activation for i in std.range(0, ff_linear_layers - 2)] + [ff_activation]),
          hidden_dims: ff_hidden,
          dropout: ([ff_dropout for i in std.range(0, ff_linear_layers - 2)] + [0]),
        },
        label_embeddings: {
          embedding_dim: ff_hidden,
          vocab_namespace: 'labels',
        },
      },
    oracle_value_function: { type: 'per-instance-f1', differentiable: false },
    score_nn: {
      type: 'multi-label-classification',
      task_nn: {
        type: 'multi-label-classification',
        feature_network: {
          input_dim: num_input_features,
          num_layers: ff_linear_layers,
          activations: ([ff_activation for i in std.range(0, ff_linear_layers - 2)] + [ff_activation]),
          hidden_dims: ff_hidden,
          dropout: ([ff_dropout for i in std.range(0, ff_linear_layers - 2)] + [0]),
        },
        label_embeddings: {
          embedding_dim: ff_hidden,
          vocab_namespace: 'labels',
        },
      },
      global_score: {
        type: 'multi-label-feedforward',
        feedforward: {
          input_dim: num_labels,
          num_layers: 1,
          activations: ff_activation,
          hidden_dims: global_score_hidden_dim,
        },
      },
    },
    loss_fn: { // for learning the score-NN
      type: 'zero-dvn-loss',
      reduction: 'mean',
      log_key: 'zero-score-loss',
    },
    initializer: {
      regexes: [
        //[@'.*_feedforward._linear_layers.0.weight', {type: 'normal'}],
        [@'score_nn.*', { type: 'pretrained', weights_file_path: '/mnt/nfs/scratch1/jaylee/repository/structured_prediction/pretrained_models/revNCE50/revNCE50.th' }],
        // [@'score_nn.*', { type: 'pretrained', weights_file_path: '/mnt/nfs/scratch1/jaylee/repository/structured_prediction/pretrained_models/revNCE_infnet/2gi3vkh6/revNCE_infnet_best.th' }],
        [@'.*feedforward._linear_layers.*weight', (if std.member(['tanh', 'sigmoid'], ff_activation) then { type: 'xavier_uniform', gain: gain } else { type: 'kaiming_uniform', nonlinearity: 'relu' })],
        [@'.*linear_layers.*bias', { type: 'zero' }],
      ],
    },
  },
  data_loader: {
    shuffle: true,
    batch_size: 32,
  },
  trainer: {
    num_epochs: if test == '1' then 150 else 300,
    //grad_norm: 10.0,
    patience: 20,
    validation_metric: '+fixed_f1',
    cuda_device: std.parseInt(cuda_device),
    learning_rate_scheduler: {
      type: 'reduce_on_plateau',
      factor: 0.5,
      mode: 'max',
      patience: 5,
      verbose: true,
    },
    optimizer: {
      lr: 0.0, #0.0036,
      weight_decay: 0.0, #7.0563993657317645e-06,
      type: 'adam',
    },
    checkpointer: {
      keep_most_recent_by_count: 1,
    },
    callbacks: [
      'track_epoch_callback',
      'decrease-xtropy-callback',
    ] + (if use_wandb then ['wandb_allennlp'] else []),
  },
}
