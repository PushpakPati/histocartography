import datetime
import logging
from typing import Dict, Optional

import mlflow
import torch
from torch.utils.data import DataLoader
from tqdm.auto import tqdm, trange

from logging_helper import LoggingHelper, prepare_experiment, robust_mlflow
from losses import get_loss
from models import PatchTissueClassifier
from utils import dynamic_import_from, get_config


def train_patch_classifier(
    dataset: str,
    model_config: Dict,
    data_config: Dict,
    metrics_config: Dict,
    batch_size: int,
    nr_epochs: int,
    num_workers: int,
    optimizer: Dict,
    loss: Dict,
    test: bool,
    validation_frequency: int,
    clip_gradient_norm: Optional[float] = None,
    **kwargs,
) -> None:
    """Train the classification model for a given number of epochs.

    Args:
        model_config (Dict): Configuration of the models (gnn and classifier)
        data_config (Dict): Configuration of the data (e.g. splits)
        batch_size (int): Batch size
        nr_epochs (int): Number of epochs to train
        optimizer (Dict): Configuration of the optimizer
    """
    logging.info(f"Unmatched arguments for pretraining: {kwargs}")

    BACKGROUND_CLASS = dynamic_import_from(dataset, "BACKGROUND_CLASS")
    NR_CLASSES = dynamic_import_from(dataset, "NR_CLASSES")
    prepare_patch_datasets = dynamic_import_from(dataset, "prepare_patch_datasets")

    # Data loaders
    training_dataset, validation_dataset = prepare_patch_datasets(**data_config)
    training_loader = DataLoader(
        training_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=batch_size,
        num_workers=num_workers,
    )

    # Compute device
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        robust_mlflow(mlflow.log_param, "device", torch.cuda.get_device_name(0))
    else:
        robust_mlflow(mlflow.log_param, "device", "CPU")

    # Model
    model = PatchTissueClassifier(num_classes=NR_CLASSES, **model_config)
    model = model.to(device)
    nr_trainable_total_params = sum(
        p.numel() for p in model.parameters() if p.requires_grad
    )
    robust_mlflow(mlflow.log_param, "nr_parameters", nr_trainable_total_params)

    # Loss function
    criterion = get_loss(loss, device=device)

    training_metric_logger = LoggingHelper(
        metrics_config,
        prefix="train",
        background_label=BACKGROUND_CLASS,
        nr_classes=NR_CLASSES,
    )
    validation_metric_logger = LoggingHelper(
        metrics_config,
        prefix="valid",
        background_label=BACKGROUND_CLASS,
        nr_classes=NR_CLASSES,
    )

    # Optimizer
    optimizer_class = dynamic_import_from("torch.optim", optimizer["class"])
    optimizer = optimizer_class(model.parameters(), **optimizer["params"])

    for epoch in trange(nr_epochs):

        # Train model
        time_before_training = datetime.datetime.now()
        model.train()
        for patches, labels in tqdm(
            training_loader, desc="train", total=len(training_loader)
        ):
            patches = patches.to(device)
            labels = labels.to(device)

            logits = model(patches)
            loss = criterion(logits, labels)

            loss.backward()
            if clip_gradient_norm is not None:
                torch.nn.utils.clip_grad.clip_grad_norm_(
                    model.parameters(), clip_gradient_norm
                )
            optimizer.step()

            training_metric_logger.add_iteration_outputs(
                losses=loss.item(),
                logits=logits.detach().cpu(),
                labels=labels.cpu(),
            )

        training_metric_logger.log_and_clear(epoch)
        training_epoch_duration = (
            datetime.datetime.now() - time_before_training
        ).total_seconds()
        robust_mlflow(
            mlflow.log_metric,
            "train.seconds_per_epoch",
            training_epoch_duration,
            step=epoch,
        )

        if epoch % validation_frequency == 0:
            # Validate model
            time_before_validation = datetime.datetime.now()
            model.eval()
            with torch.no_grad():
                for patches, labels in tqdm(
                    validation_loader, desc="valid", total=len(validation_loader)
                ):
                    patches = patches.to(device)
                    labels = labels.to(device)

                    logits = model(patches)
                    loss = criterion(logits, labels)

                    validation_metric_logger.add_iteration_outputs(
                        losses=loss.item(),
                        logits=logits.detach().cpu(),
                        labels=labels.cpu(),
                    )

            validation_metric_logger.log_and_clear(
                epoch, model=model if not test else None
            )
            validation_epoch_duration = (
                datetime.datetime.now() - time_before_validation
            ).total_seconds()
            robust_mlflow(
                mlflow.log_metric,
                "valid.seconds_per_epoch",
                validation_epoch_duration,
                step=epoch,
            )


if __name__ == "__main__":
    config, config_path, test = get_config(
        name="train",
        default="pretrain.yml",
        required=("model", "data", "metrics", "params"),
    )
    logging.info("Start pre-training")
    if test:
        config["data"]["overfit_test"] = True
        config["params"]["num_workers"] = 0
    prepare_experiment(config_path=config_path, **config)
    train_patch_classifier(
        model_config=config["model"],
        data_config=config["data"],
        metrics_config=config["metrics"],
        config_path=config_path,
        test=test,
        **config["params"],
    )