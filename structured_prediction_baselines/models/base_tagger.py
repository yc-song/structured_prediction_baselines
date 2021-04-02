from typing import Dict, Optional, List, Any, cast
import sys

if sys.version_info >= (3, 8):
    from typing import (
        TypedDict,
    )  # pylint: disable=no-name-in-module
else:
    from typing_extensions import TypedDict

from overrides import overrides
import torch
from torch.nn.modules.linear import Linear

from allennlp.common.checks import check_dimensions_match, ConfigurationError
from allennlp.data import TextFieldTensors, Vocabulary
from allennlp.modules import Seq2SeqEncoder, TimeDistributed, TextFieldEmbedder
from allennlp.modules import ConditionalRandomField, FeedForward
from allennlp.modules.conditional_random_field import allowed_transitions
from allennlp.models.model import Model
from allennlp.nn import InitializerApplicator
import allennlp.nn.util as util
from allennlp.training.metrics import CategoricalAccuracy, SpanBasedF1Measure


@Model.register("base_tagger")
class BaseTagger(Model):
    """
    The base class for implementing sequence tagger.

    It involves the following generalized modules:

    1. FeedForward Module (task NN):

        a. Encoder Stack (F): Encodes the text using a `TextFieldEmbedder` and `Seq2SeqEncoder`. Lets call this F(x).

        b. Mapping Module (L): Produces logits

    2. Inference: Takes the logits produces candidate outputs through inference.
        The candidate outputs will have shape (batch, sequence, num_classes, num_samples).

        a.  we can just return argmax as a single sample.
            The output shape will be (batch, seq, num_classes, 1), where num_classes is one-hot.

        b.  we can do sampling assuming each step is independent.
            The output shape will be (batch, seq, num_classes, num_samples), where num_classes is one-hot.

        c.  We can do a beam search.
            The output shape will be (batch, seq, num_classes, num_samples), where num_classes is one-hot.

        CANCEL  d.  We can use inference network setup where we have
                an inference network phi that will produce logits (note this can be identity).
                Here, we will have to train the parameters of phi using
                a differentiable cost function D(y, y*) = D(phi(x*), y*), and
                an energy function E(x*,y),
                using the objective: max_phi [D(phi(x*), y*) -E(x*, phi(x*)) + E(x*,y*)].
                This will back propagate to L and F as well.
                The output shape will be (batch, seq, num_classes, 1), where num_classes is NOT one-hot,
                but it sums to 1.


    3. Energy/Value/Score Module (E): The energy module will contain the some encoder stack (like F and L) but with
        separate parameters. It will have various structured components like linear chaing (CRF), etc on top of
        the encoder stack (like described in Arbitrary order seq labeling paper). The energy module takes in
        raw input x* and some y (output similar in shape to that of inference module) and produces a scaler score.

        Interface (input/output):
            Input:


        class TaskNN(nn.Module, Registrable)


        class ScoreNN(TaskNN)

        class ScoreNN(TaskNN):
            def __init__(self, encoder: Encoder,
                stuctured_energy: StructuredEnergy):

                self.embedder =


        class StructuredEnergy(nn.Module, Registrable)
            pass

        @StructuredEnergy.register('linear-chain')
        class LinearChain(StructuredEnergy):
            def __init__(self, num_tags, hidden_params, window_size):
                self.W = torch.nn.Parameter(num_tags, hidden_params)

            def forward(self, y_hat, y, mask):
                ...

        class VKP(nn.Module):
            def forward(self, logits, y, mask):
                ...

        y_hat = FF(x)

        E(y_hat, labels, mask)


    4. Cost module (D): Could be differentiable or not.

    5. Main Loss (L): This is where we update the parameters of the energy/value (E).


    6. Sampler (S): For generating samples during training.
        This case is slightly different from all the above. This module is where we update the
        parameters of the inference module (phi, L,F) or we perform loss augmented inference in
        y space.
        We can do cost augmented inference
        using the objective argmin_y E(x*,y) - D(y, y*) and gradient
        descent on y. Here, again we will need a
        differentiable cost function D.
        Note that, here the input logits will not be used. Hence, F and L need not be present when
        using SPENs with cost augmented inference.
        Moreover, F and L will not get gradients
        from here.
        The output shape will be (batch, seq, num_classes, 1), where num_classes is NOT one-hot,
        but it sums to 1.




    # Parameters

    vocab : `Vocabulary`, required
        A Vocabulary, required in order to compute sizes for input/output projections.
    text_field_embedder : `TextFieldEmbedder`, required
        Used to embed the tokens `TextField` we get as input to the model.
    encoder : `Seq2SeqEncoder`
        The encoder that we will use in between embedding tokens and predicting output tags.
    label_namespace : `str`, optional (default=`labels`)
        This is needed to compute the SpanBasedF1Measure metric.
        Unless you did something unusual, the default value should be what you want.
    feedforward : `FeedForward`, optional, (default = `None`).
        An optional feedforward layer to apply after the encoder.
    label_encoding : `str`, optional (default=`None`)
        Label encoding to use when calculating span f1 and constraining
        the CRF at decoding time . Valid options are "BIO", "BIOUL", "IOB1", "BMES".
        Required if `calculate_span_f1` or `constrain_crf_decoding` is true.
    include_start_end_transitions : `bool`, optional (default=`True`)
        Whether to include start and end transition parameters in the CRF.
    constrain_crf_decoding : `bool`, optional (default=`None`)
        If `True`, the CRF is constrained at decoding time to
        produce valid sequences of tags. If this is `True`, then
        `label_encoding` is required. If `None` and
        label_encoding is specified, this is set to `True`.
        If `None` and label_encoding is not specified, it defaults
        to `False`.
    calculate_span_f1 : `bool`, optional (default=`None`)
        Calculate span-level F1 metrics during training. If this is `True`, then
        `label_encoding` is required. If `None` and
        label_encoding is specified, this is set to `True`.
        If `None` and label_encoding is not specified, it defaults
        to `False`.
    dropout:  `float`, optional (default=`None`)
        Dropout probability.
    verbose_metrics : `bool`, optional (default = `False`)
        If true, metrics will be returned per label class in addition
        to the overall statistics.
    initializer : `InitializerApplicator`, optional (default=`InitializerApplicator()`)
        Used to initialize the model parameters.
    top_k : `int`, optional (default=`1`)
        If provided, the number of parses to return from the crf in output_dict['top_k_tags'].
        Top k parses are returned as a list of dicts, where each dictionary is of the form:
        {"tags": List, "score": float}.
        The "tags" value for the first dict in the list for each data_item will be the top
        choice, and will equal the corresponding item in output_dict['tags']
    """

    class Buffer(TypedDict, total=False):
        mask: torch.BoolTensor

    def __init__(
        self,
        vocab: Vocabulary,
        text_field_embedder: TextFieldEmbedder,
        encoder: Seq2SeqEncoder,
        label_namespace: str = "labels",
        feedforward: Optional[FeedForward] = None,
        label_encoding: Optional[str] = None,
        include_start_end_transitions: bool = True,
        constrain_crf_decoding: bool = None,
        calculate_span_f1: bool = None,
        dropout: Optional[float] = None,
        verbose_metrics: bool = False,
        initializer: InitializerApplicator = InitializerApplicator(),
        top_k: int = 1,
        **kwargs,
    ) -> None:
        super().__init__(vocab, **kwargs)

        self.label_namespace = label_namespace
        self.text_field_embedder = text_field_embedder
        self.num_tags = self.vocab.get_vocab_size(label_namespace)
        self.encoder = encoder
        self.top_k = top_k
        self._verbose_metrics = verbose_metrics

        if dropout:
            self.dropout = torch.nn.Dropout(dropout)
        else:
            self.dropout = None
        self._feedforward = feedforward

        if feedforward is not None:
            output_dim = feedforward.get_output_dim()
        else:
            output_dim = self.encoder.get_output_dim()
        self.tag_projection_layer = TimeDistributed(
            Linear(output_dim, self.num_tags)
        )

        # if  constrain_crf_decoding and calculate_span_f1 are not
        # provided, (i.e., they're None), set them to True
        # if label_encoding is provided and False if it isn't.

        if constrain_crf_decoding is None:
            constrain_crf_decoding = label_encoding is not None

        if calculate_span_f1 is None:
            calculate_span_f1 = label_encoding is not None

        self.label_encoding = label_encoding

        if constrain_crf_decoding:
            if not label_encoding:
                raise ConfigurationError(
                    "constrain_crf_decoding is True, but no label_encoding was specified."
                )
            labels = self.vocab.get_index_to_token_vocabulary(label_namespace)
            constraints = allowed_transitions(label_encoding, labels)
        else:
            constraints = None

        self.include_start_end_transitions = include_start_end_transitions
        self.crf = ConditionalRandomField(
            self.num_tags,
            constraints,
            include_start_end_transitions=include_start_end_transitions,
        )

        self.metrics = {
            "accuracy": CategoricalAccuracy(),
            "accuracy3": CategoricalAccuracy(top_k=3),
        }
        self.calculate_span_f1 = calculate_span_f1

        if calculate_span_f1:
            if not label_encoding:
                raise ConfigurationError(
                    "calculate_span_f1 is True, but no label_encoding was specified."
                )
            self._f1_metric = SpanBasedF1Measure(
                vocab,
                tag_namespace=label_namespace,
                label_encoding=label_encoding,
            )

        check_dimensions_match(
            text_field_embedder.get_output_dim(),
            encoder.get_input_dim(),
            "text field embedding dim",
            "encoder input dim",
        )

        if feedforward is not None:
            check_dimensions_match(
                encoder.get_output_dim(),
                feedforward.get_input_dim(),
                "encoder output dim",
                "feedforward input dim",
            )
        initializer(self)

    def get_logits(
        self,
        tokens: TextFieldEmbedder,
        tags: torch.LongTensor = None,
        buffer: Dict = None,
    ) -> torch.Tensor:
        if not buffer:
            buffer = {}
        embedded_text_input = self.text_field_embedder(tokens)
        mask = util.get_text_field_mask(tokens)
        buffer["mask"] = mask

        if self.dropout:
            embedded_text_input = self.dropout(embedded_text_input)

        encoded_text = self.encoder(embedded_text_input, mask)

        if self.dropout:
            encoded_text = self.dropout(encoded_text)

        if self._feedforward is not None:
            encoded_text = self._feedforward(encoded_text)

        logits = self.tag_projection_layer(encoded_text)

        return logits

    @overrides
    def forward(
        self,  # type: ignore
        tokens: TextFieldTensors,
        tags: torch.LongTensor = None,
        metadata: List[Dict[str, Any]] = None,
        ignore_loss_on_o_tags: bool = False,
        **kwargs,  # to allow for a more general dataset reader that passes args we don't need
    ) -> Dict[str, torch.Tensor]:

        """
        # Parameters

        tokens : `TextFieldTensors`, required
            The output of `TextField.as_array()`, which should typically be passed directly to a
            `TextFieldEmbedder`. This output is a dictionary mapping keys to `TokenIndexer`
            tensors.  At its most basic, using a `SingleIdTokenIndexer` this is : `{"tokens":
            Tensor(batch_size, num_tokens)}`. This dictionary will have the same keys as were used
            for the `TokenIndexers` when you created the `TextField` representing your
            sequence.  The dictionary is designed to be passed directly to a `TextFieldEmbedder`,
            which knows how to combine different word representations into a single vector per
            token in your input.
        tags : `torch.LongTensor`, optional (default = `None`)
            A torch tensor representing the sequence of integer gold class labels of shape
            `(batch_size, num_tokens)`.
        metadata : `List[Dict[str, Any]]`, optional, (default = `None`)
            metadata containg the original words in the sentence to be tagged under a 'words' key.
        ignore_loss_on_o_tags : `bool`, optional (default = `False`)
            If True, we compute the loss only for actual spans in `tags`, and not on `O` tokens.
            This is useful for computing gradients of the loss on a _single span_, for
            interpretation / attacking.

        # Returns

        An output dictionary consisting of:

        logits : `torch.FloatTensor`
            The logits that are the output of the `tag_projection_layer`
        mask : `torch.BoolTensor`
            The text field mask for the input tokens
        tags : `List[List[int]]`
            The predicted tags using the Viterbi algorithm.
        loss : `torch.FloatTensor`, optional
            A scalar loss to be optimised. Only computed if gold label `tags` are provided.
        """
        buffer = self.Buffer()

        # text encoding
        logits = self.get_logits(
            tokens, tags, buffer=buffer
        )  # (batch, seq, num_tags)
        assert "mask" in buffer, "get_logits should add mask to the buffer"
        mask = buffer["mask"]

        ### generalize this to get_samples method
        best_paths = self.crf.viterbi_tags(
            logits, buffer["mask"], top_k=self.top_k
        )  # (batch, seq_len)
        # we might want to add an extra dim to hold multiple samples

        # Just get the top tags and ignore the scores.
        predicted_tags = cast(List[List[int]], [x[0][0] for x in best_paths])

        output = {
            "logits": logits,
            "mask": buffer["mask"],
            "tags": predicted_tags,
        }

        if self.top_k > 1:
            output["top_k_tags"] = best_paths

        if tags is not None:
            if ignore_loss_on_o_tags:
                o_tag_index = self.vocab.get_token_index(
                    "O", namespace=self.label_namespace
                )
                crf_mask = mask & (tags != o_tag_index)
            else:
                crf_mask = mask
            # take predicted_tags and get the log-likelihood

            # Add negative log-likelihood as loss
            # Generalize this call to take logits, tags, mask
            # to get log-likelihood
            log_likelihood = self.crf(logits, tags, crf_mask)

            # multiply score(predicted_tags, tags)*log_likelihood(predicted_tags)
            output["loss"] = -log_likelihood

            # Represent viterbi tags as "class probabilities" that we can
            # feed into the metrics
            class_probabilities = logits * 0.0

            for i, instance_tags in enumerate(predicted_tags):
                for j, tag_id in enumerate(instance_tags):
                    class_probabilities[i, j, tag_id] = 1

            for metric in self.metrics.values():
                metric(class_probabilities, tags, mask)

            if self.calculate_span_f1:
                self._f1_metric(class_probabilities, tags, mask)

        if metadata is not None:
            output["words"] = [x["words"] for x in metadata]

        return output

    @overrides
    def make_output_human_readable(
        self, output_dict: Dict[str, torch.Tensor]
    ) -> Dict[str, torch.Tensor]:
        """
        Converts the tag ids to the actual tags.
        `output_dict["tags"]` is a list of lists of tag_ids,
        so we use an ugly nested list comprehension.
        """

        def decode_tags(tags):
            return [
                self.vocab.get_token_from_index(
                    tag, namespace=self.label_namespace
                )
                for tag in tags
            ]

        def decode_top_k_tags(top_k_tags):
            return [
                {"tags": decode_tags(scored_path[0]), "score": scored_path[1]}
                for scored_path in top_k_tags
            ]

        output_dict["tags"] = [decode_tags(t) for t in output_dict["tags"]]

        if "top_k_tags" in output_dict:
            output_dict["top_k_tags"] = [
                decode_top_k_tags(t) for t in output_dict["top_k_tags"]
            ]

        return output_dict

    @overrides
    def get_metrics(self, reset: bool = False) -> Dict[str, float]:
        metrics_to_return = {
            metric_name: metric.get_metric(reset)
            for metric_name, metric in self.metrics.items()
        }

        if self.calculate_span_f1:
            f1_dict = self._f1_metric.get_metric(reset=reset)

            if self._verbose_metrics:
                metrics_to_return.update(f1_dict)
            else:
                metrics_to_return.update(
                    {x: y for x, y in f1_dict.items() if "overall" in x}
                )

        return metrics_to_return

    default_predictor = "sentence_tagger"
