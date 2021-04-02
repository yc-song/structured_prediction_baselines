from .task_nn import TaskNN
from typing import List, Tuple, Union, Dict, Any, Optional
import torch
from allennlp.data import TextFieldTensors, Vocabulary
from allennlp.modules import (
    Seq2SeqEncoder,
    TimeDistributed,
    TextFieldEmbedder,
    FeedForward,
)
from torch.nn.modules.linear import Linear
import torch.nn.functional as F
import allennlp.nn.util as util


@TaskNN.register("seq-tagging-task")
class SequenceTaggingTaskNN(TaskNN):
    def __init__(
        self,
        text_field_embedder: TextFieldEmbedder,
        num_tags: int,
        output_dim: int = None,
        encoder: Optional[Seq2SeqEncoder] = None,
        feedforward: Optional[FeedForward] = None,
        dropout: float = 0,
    ):
        """

        Args:
            text_field_embedder : `TextFieldEmbedder`, required
                Used to embed the tokens `TextField` we get as input to the model.
            encoder : `Seq2SeqEncoder`
                The encoder that we will use in between embedding tokens and predicting output tags.
            feedforward : `FeedForward`, optional, (default = `None`).
                An optional feedforward layer to apply after the encoder.

        """
        super().__init__()
        self.num_tags = num_tags
        self.text_field_embedder = text_field_embedder
        self.encoder = encoder
        self.feedforward = feedforward

        if feedforward is not None:
            output_dim = feedforward.get_output_dim()  # type: ignore
        elif encoder is not None:
            output_dim = self.encoder.get_output_dim()

        if output_dim is None:
            raise ValueError("output_dim cannot be None")

        self.tag_projection_layer = TimeDistributed(  # type: ignore
            torch.nn.Sequential(
                Linear(output_dim, num_tags, bias=False),
                torch.nn.Softmax(dim=-1),
            )
        )

        if dropout:
            self.dropout: Optional[torch.nn.Module] = torch.nn.Dropout(dropout)
        else:
            self.dropout = None

    def forward(
        self,  # type: ignore
        tokens: TextFieldTensors,
        buffer: Dict = None,
    ) -> torch.Tensor:
        if buffer is None:
            buffer = {}
            mask = util.get_text_field_mask(tokens)
            mask = mask.unsqueeze(dim=1)  # (batch_size, 1, ...)
            buffer["mask"] = mask

        embedded_text_input = self.text_field_embedder(tokens)

        if self.encoder:
            encoded_text = self.encoder(embedded_text_input, buffer["mask"])
        else:
            encoded_text = embedded_text_input

        if self.dropout:
            encoded_text = self.dropout(encoded_text)

        if self.feedforward:
            encoded_text = self.feedforward(encoded_text)

        logits = self.tag_projection_layer(encoded_text)

        return logits  # shape (batch, 1, sequence, num_tags)
