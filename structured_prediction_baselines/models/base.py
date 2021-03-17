from typing import List, Tuple, Union, Dict, Any, Optional
import torch
from allennlp.models import Model
from structured_prediction_baselines.modules.sampler import Sampler
from structured_prediction_baselines.modules.oracle_value_function import (
    OracleValueFunction,
)
from structured_prediction_baselines.modules.score_nn import ScoreNN
from structured_prediction_baselines.modules.loss import Loss
from allennlp.data.vocabulary import Vocabulary
from allennlp.common.lazy import Lazy
from allennlp.nn import InitializerApplicator, RegularizerApplicator


@Model.register("score-based-learning", constructor="from_partial_objects")
class ScoreBasedLearningModel(Model):
    def __init__(
        self,
        vocab: Vocabulary,
        sampler: Sampler,
        loss_fn: Loss,
        oracle_value_function: Optional[OracleValueFunction] = None,
        score_nn: Optional[ScoreNN] = None,
        inference_module: Optional[Sampler] = None,
        regularizer: Optional[RegularizerApplicator] = None,
        initializer: Optional[InitializerApplicator] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(vocab, regularizer=regularizer)  # type:ignore
        self.sampler = sampler
        self.loss_fn = loss_fn
        self.oracle_value_function = oracle_value_function
        self.score_nn = score_nn

        if inference_module is not None:
            self.inference_module = inference_module
        else:
            self.inference_module = sampler

        if initializer is not None:
            initializer(self)

    @classmethod
    def from_partial_objects(
        cls,
        vocab: Vocabulary,
        loss_fn: Lazy[Loss],
        sampler: Lazy[Sampler],
        inference_module: Optional[Lazy[Sampler]] = None,
        score_nn: Optional[ScoreNN] = None,
        oracle_value_function: Optional[OracleValueFunction] = None,
        regularizer: Optional[RegularizerApplicator] = None,
        initializer: Optional[InitializerApplicator] = None,
        **kwargs: Any,
    ) -> "ScoreBasedLearningModel":
        sampler_ = sampler.construct(
            score_nn=score_nn, oracle_value_function=oracle_value_function
        )
        loss_fn_ = loss_fn.construct(
            score_nn=score_nn, oracle_value_function=oracle_value_function
        )
        # if no seperate inference module is given,
        # we will be using the same sampler

        if inference_module is None:
            inference_module_ = sampler_
        else:
            inference_module_ = inference_module.construct(
                score_nn=score_nn, oracle_value_function=oracle_value_function
            )

        return cls(
            vocab=vocab,
            sampler=sampler_,
            loss_fn=loss_fn_,
            oracle_value_function=oracle_value_function,
            score_nn=score_nn,
            inference_module=inference_module_,
            regularizer=regularizer,
            initializer=initializer,
            **kwargs,
        )

    def calculate_metrics(
        self, labels: torch.Tensor, y_hat: torch.Tensor, **kwargs: Any
    ) -> None:
        return None

    def get_extra_args_for_loss(
        self,
        x: Any,
        labels: torch.Tensor,
        **kwargs: Any,
    ) -> Dict:
        return {}

    def convert_to_one_hot(self, labels: torch.Tensor) -> torch.Tensor:
        """Converts the labels to one-hot if not already"""

        return labels

    def unsqueeze_labels(self, labels: torch.Tensor) -> torch.Tensor:
        """Unsqueeze if required"""

        return labels

    def forward(  # type: ignore
        self,
        x: Any,
        labels: torch.Tensor,
        meta: Optional[Dict] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:

        if meta is None:
            meta = {}
        results: Dict[str, Any] = {}
        # sampler needs one-hot labels of shape (batch, ...)

        if labels is not None:
            labels = self.convert_to_one_hot(labels)
            y_hat, probabilities = self.sampler(x, labels)
            # (batch, num_samples or 1, ...), (batch, num_samples or 1)
            results["y_hat"] = y_hat
            results["sample_probabilities"] = probabilities

            # prepare for calculating metrics
            # y_pred is predictions for metric calculations
            # y_hat are for loss computation
            # For some models this two can be different

            if (self.sampler != self.inference_module) or (
                self.inference_module.different_training_and_eval
            ):
                # we have different sampler for training and inference
                # or the sampler behaves differently
                # so we need to run it again.
                # Note: It is vital to set the module in the eval mode
                # ie with .training = False because the implementation
                # checks this
                model_state = self.training
                self.inference_module.eval()
                y_pred, _ = self.inference_module(x)
                self.inference_module.train(model_state)
            else:
                y_pred = y_hat
            # Loss needs one-hot labels of shape (batch, 1, ...)
            labels = self.unsqueeze_labels(labels)
            loss = self.loss_fn(
                x,
                labels,
                y_hat,
                probabilities,
                **self.get_extra_args_for_loss(
                    x, labels, **kwargs
                ),  # used to compute mask if needed.
            )
            results["loss"] = loss
            self.calculate_metrics(labels, y_pred)
        else:
            # labels not present. Just predict.
            model_state = self.training
            self.inference_module.eval()
            y_pred, _ = self.inference_module(x)
            self.inference_module.train(model_state)

        results["y_pred"] = y_pred

        return results
