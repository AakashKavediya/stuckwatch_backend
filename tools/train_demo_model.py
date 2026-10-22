"""Optional scikit-learn LogisticRegression training script for stuckwatch.

Demonstrates fitting a transparent linear model on synthetic feature vectors
and exporting weights/joblib model. By default, stuckwatch uses its built-in
transparent rule-based weights in config.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
import joblib


def generate_synthetic_training_data(n_samples: int = 1000):
    """Generate synthetic evidence vectors and binary labels."""
    np.random.seed(42)
    # Features:
    # 0: screen_stagnation (0 to 1)
    # 1: erratic_switching (0 to 1)
    # 2: fixed_gaze (0 to 1)
    # 3: blink_anomaly (0 to 1)
    # 4: frozen_posture (0 to 1)

    X = np.random.uniform(0.0, 1.0, size=(n_samples, 5))

    # True latent weights with realistic prior bias
    w_true = np.array([1.8, 1.4, 1.2, 0.9, 1.0])
    b_true = -2.2

    logits = np.dot(X, w_true) + b_true + np.random.normal(0, 0.2, size=n_samples)
    probs = 1.0 / (1.0 + np.exp(-logits))
    y = (probs >= 0.5).astype(int)

    return X, y


def main():
    print("[stuckwatch train] Generating synthetic training data...")
    X, y = generate_synthetic_training_data(2000)

    print(f"[stuckwatch train] Fitting LogisticRegression on {len(X)} samples...")
    clf = LogisticRegression(C=1.0)
    clf.fit(X, y)

    print(f"[stuckwatch train] Model trained successfully!")
    print(f"  Intercept (bias): {clf.intercept_[0]:.3f}")
    feature_names = [
        "screen_stagnation",
        "erratic_switching",
        "fixed_gaze",
        "blink_anomaly",
        "frozen_posture",
    ]
    for name, coef in zip(feature_names, clf.coef_[0]):
        print(f"  Weight for {name}: {coef:.3f}")

    out_dir = Path(__file__).resolve().parent.parent / ".stuckwatch_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "model.joblib"
    joblib.dump(clf, out_path)
    print(f"[stuckwatch train] Model saved to: {out_path}")


if __name__ == "__main__":
    main()
