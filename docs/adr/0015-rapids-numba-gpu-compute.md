# ADR 0015: RAPIDS + Numba GPU Compute Stack

## Status
Proposed

## Context
ATI's feature engineering runs on CPU (pandas/NumPy). At 100K+ events/sec with rolling windows (OHLCV, VWAP, TWAP, order-book features), CPU becomes the bottleneck. Research confirms RAPIDS cuDF/cuGraph/cuML (Apache 2.0, Python 3.14 native since 26.04) + Numba CUDA (BSD-2, Python 3.14 native since 0.24) provide 10-100x speedup for tabular market data and custom kernels.

## Decision
Adopt RAPIDS + Numba as the GPU compute foundation:
- `cuDF` + `cudf.pandas` for zero-code-change DataFrame acceleration
- `cuML` for covariance, PCA, UMAP (embedding generation)
- `Numba CUDA` (`@cuda.jit`, `@cuda.reduce`) for fused rolling-window kernels (VWAP, TWAP, microstructure)
- `JAX` for differentiable risk (CVaR gradients) and TPU option
- `PyTorch` for deep learning features (future)
- `VectorBT` for CPU backtesting (pair with GPU feature store)
- Container: `nvcr.io/nvidia/rapidsai/rapidsai:26.06-cuda13-py3.14`
- Driver: 580+ (CUDA 13)

## Consequences
- **Positive**: 10-100x feature engineering throughput; Python 3.14 native; permissive licenses; mature
- **Negative**: NVIDIA GPU required (CUDA 13); ~8GB container; driver/container orchestration complexity
- **Neutral**: CPU fallback via `cudf.pandas` zero-copy; no code change for pandas users

## Integration Record
- Component: `GpuFeatureEngine`, `GpuRiskCalculator`, `GpuEmbeddingService`
- Purpose: GPU-accelerated feature engineering, risk, embeddings
- Category: Compute Acceleration
- Version: `rapids=26.06`, `numba-cuda=0.24`, `jax[cuda13]=0.10`, `torch[cuda13]=2.12`
- Source: https://github.com/rapidsai, https://github.com/numba/numba, https://github.com/google/jax, https://github.com/pytorch/pytorch
- License: Apache 2.0 / BSD-2 / BSD-3 / MIT
- Status: Planned
- Priority: Medium (after core pipeline stable)
- Entrypoint: `backend/application/gpu/` (new module)
- Dependencies: NVIDIA driver 580+, CUDA 13, RAPIDS container
- Capabilities: Rolling windows 100K+ events/sec, covariance 10K assets, embedding generation, Monte Carlo VaR
- Configuration: `GpuConfig(enabled, device_id, memory_fraction, fallback_cpu)`
- Health: GPU reachable, memory < 90%, kernel compilation cache hit > 80%
- Upgrade Path: RAPIDS bi-weekly releases; CUDA minor version pin; Numba/JAX/PyTorch version matrix
- Reason: Only mature, Python 3.14-native, permissively-licensed GPU stack covering tabular + custom kernels + ML

## Validation Gate
- `cudf.pandas` drops in for current pandas feature code with < 5% code change
- Rolling OHLCV/VWAP at 100K events/sec < 10ms on H100
- Covariance 10K assets < 100ms
- Monte Carlo VaR 10K paths < 50ms
- CPU fallback produces bit-identical results (determinism)

## References
- ADR 0014 (NATS JetStream — enables multi-process GPU workers)
- ARCHITECTURE_REVIEW.md §326-330 (Performance Review)
- docs/Constitution/04-Engineering-Standards.md (Optimize only where meaningful)