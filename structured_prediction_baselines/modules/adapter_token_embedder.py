from typing import List, Tuple, Union, Dict, Any, Optional
from allennlp.modules.token_embedders import (
    PretrainedTransformerEmbedder,
    TokenEmbedder,
)


@TokenEmbedder.register("pretrained_transformer_with_adapter")
class PretrainedTransformerWithAdapterEmbedder(PretrainedTransformerEmbedder):
    def __init__(
        self, *args: Any, adapter_config: str = "pfeiffer", **kwargs: Any
    ):
        kwargs["train_parameters"] = False
        super().__init__(*args, **kwargs)
        self.adapter_config = adapter_config
        self.adapter_name = "embedder_adapter"
        self.transformer_model.add_adapter(
            self.adapter_name, config=adapter_config
        )
        self.transformer_model.set_active_adapters("embedder_adapter")

    def train(
        self: "PretrainedTransformerWithAdapterEmbedder", mode: bool = True
    ) -> "PretrainedTransformerWithAdapterEmbedder":
        if mode:
            self.transformer_model.train_adapter([self.adapter_name])

        super().train(mode)

        return self
