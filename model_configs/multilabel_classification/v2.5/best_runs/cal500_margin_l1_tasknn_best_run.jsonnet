// Run ID: ntkaa8xb

local test = std.extVar('TEST');  // a test run with small dataset
local data_dir = std.extVar('DATA_DIR');
local cuda_device = std.extVar('CUDA_DEVICE');
local use_wandb = (if test == '1' then false else true);

local dataset_name = 'cal500';
local dataset_metadata = (import '../../datasets.jsonnet')[dataset_name];
local num_labels = dataset_metadata.num_labels;
local num_input_features = dataset_metadata.input_features;

// model variables
local ff_hidden = 500; //std.parseJson(std.extVar('ff_hidden'));
local label_space_dim = ff_hidden;
local ff_dropout = 0.2; //std.parseJson(std.extVar('ff_dropout'));
local ff_activation = 'softplus';
local ff_linear_layers = 5; //std.parseJson(std.extVar('ff_linear_layers'));
local ff_weight_decay = 0.00001; //std.parseJson(std.extVar('ff_weight_decay'));
local global_score_hidden_dim = 400; //std.parseJson(std.extVar('global_score_hidden_dim'));
local gain = (if ff_activation == 'tanh' then 5 / 3 else 1);
local cross_entropy_loss_weight = 3.992; //std.parseJson(std.extVar('cross_entropy_loss_weight'));
local inference_score_weight = 0.004897; //std.parseJson(std.extVar('inference_score_weight'));
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
    type: 'multi-label-classification-with-infnet-and-scorenn-evaluation',
    sampler: {
      type: 'appending-container',
      log_key: 'sampler',
      constituent_samplers: [],
    },
    evaluation_module: {
      type: 'indexed-container',
      log_key: 'evaluation',
      constituent_samplers: [
        {
          type: 'gradient-based-inference',
          log_key: 'tasknn_gbi',
          gradient_descent_loop: {
            optimizer: {
              lr: 0.1,  //0.1
              weight_decay: 0,
              type: 'sgd',
            },
          },
          loss_fn: { type: 'multi-label-dvn-score', reduction: 'none', log_key: 'neg.dvn_score'},
          output_space: { type: 'multi-label-relaxed', num_labels: num_labels, default_value: 0.0 },
          stopping_criteria: 20,
          sample_picker: { type: 'best' },  // {type: 'best'}
        },
        {
          type: 'gradient-based-inference',
          log_key: 'random_gbi',
          gradient_descent_loop: {
            optimizer: {
              lr: 0.1,  //0.1
              weight_decay: 0,
              type: 'sgd',
            },
          },
          loss_fn: { type: 'multi-label-dvn-score', reduction: 'none', log_key: 'neg.dvn_score'},
          output_space: { type: 'multi-label-relaxed', num_labels: num_labels, default_value: 0.0 },
          stopping_criteria: 20,
          sample_picker: { type: 'best' },  // {type: 'best'}
        },
      ],
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

    inference_module: {
      type: 'multi-label-inference-net-normalized',
      log_key: 'inference_module',
      cost_augmented_layer: {
        type: 'multi-label-stacked',
        feedforward: {
          input_dim: 2 * num_labels,
          num_layers: 2,
          activations: [ff_activation, 'linear'],
          hidden_dims: num_labels,
        },
        normalize_y: true,
      },
      loss_fn: {
        type: 'combination-loss',
        log_key: 'loss',
        constituent_losses: [
          {
            type: 'multi-label-inference',
            log_key: 'neg_inference',
            normalize_y: true,
            reduction: 'none',
            inference_score_weight: inference_score_weight,
          },  //This loss can be different from the main loss // change this
          {
            type: 'multi-label-bce',
            reduction: 'none',
            log_key: 'bce',
          },
        ],
        loss_weights: [1.0, cross_entropy_loss_weight],
        reduction: 'mean',
      },
    },
    oracle_value_function: {
      type: 'manhattan',
      differentiable: true,
    },
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
    loss_fn: {
      type: 'multi-label-margin-based',
      oracle_cost_weight: 1.0,
      perceptron_loss_weight: inference_score_weight,
      reduction: 'mean',
      log_key: 'margin_loss',
    },
    initializer: {
      regexes: [
        //[@'.*_feedforward._linear_layers.0.weight', {type: 'normal'}],
        [@'.*_linear_layers.*weight', (if std.member(['tanh', 'sigmoid'], ff_activation) then { type: 'xavier_uniform', gain: gain } else { type: 'kaiming_uniform', nonlinearity: 'relu' })],
        [@'.*linear_layers.*bias', { type: 'zero' }],
      ],
    },
  },
  data_loader: {
    shuffle: true,
    batch_size: 32,
  },
  trainer: {
    type: 'gradient_descent_minimax',
    num_epochs: if test == '1' then 10 else 300,
    grad_norm: { task_nn: 10.0 },
    patience: 20,
    validation_metric: '+fixed_f1',
    cuda_device: std.parseInt(cuda_device),
    learning_rate_schedulers: {
      task_nn: {
        type: 'reduce_on_plateau',
        factor: 0.5,
        mode: 'max',
        patience: 5,
        verbose: true,
      },
    },
    optimizer: {
      optimizers: {
        task_nn:
          {
            lr: 0.008267,
            weight_decay: ff_weight_decay,
            type: 'adamw',
          },
        score_nn: {
          lr: 0.0005102,
          weight_decay: ff_weight_decay,
          type: 'adamw',
        },
      },
    },
    checkpointer: {
      keep_most_recent_by_count: 1,
    },
    callbacks: [
      'track_epoch_callback',
      'slurm',
    ] + (
      if use_wandb then [
        {
          type: 'wandb_allennlp',
          sub_callbacks: [{ type: 'log_best_validation_metrics', priority: 100 }],
        },
      ]
      else []
    ),
    inner_mode: 'task_nn',
    num_steps: { task_nn: 10, score_nn: 1 },
  },
}
