# Pixel Error Detection for Video Quality Control

Netflix’s video quality control (QC) process has historically relied on manual frame-by-frame inspection to catch transient artifacts—most notably “hot pixels,” which are single‑frame bright spots caused by malfunctioning camera sensors. Because these errors occur at very low frequency and are often invisible at reduced resolution, the manual review of high‑resolution (4K and above) footage is both time‑consuming and error‑prone. Missed artifacts can surface later in production, driving up costs and degrading the viewing experience.

To address this, the QC team built an automated detection system based on a custom neural network that operates in real time on a single GPU. The system is designed to analyze video at full resolution—deliberately avoiding downsampling, because pixel‑level defects become nearly invisible when content is scaled to 480p. The model processes a temporal window of five consecutive frames to exploit context: naturally bright objects like streetlights or specular reflections persist across frames, whereas sensor glitches appear in only one frame. This distinction allows the model to suppress false positives while retaining high sensitivity.

At the core of the network is a dense pixel‑wise prediction head that outputs a continuous‑valued error map at the input resolution. During training, it is supervised with a pixel‑wise loss function. At inference, the error map is binarized using a confidence threshold, then passed through a connected‑component labeling step to cluster adjacent true‑positive pixels. The centroids of these clusters are computed and reported as (x, y) coordinates, which can be fed directly into downstream QC workflows.

## Synthetic Data Generation and the Refinement Loop

A major challenge was the rarity of pixel errors in real footage, making manual annotation impractical. Instead, the team built a synthetic pixel error generator that simulated two error types—symmetrical and curvilinear—and superimposed them onto real Netflix catalog footage. To maximize training signal, the synthetic errors were placed strategically in dark, still regions where they are most visually salient. A heatmap based on motion and intensity was used to sample candidate locations.

The model was then trained using a synthetic‑to‑real refinement loop:

1. **Initial training** – The model is trained purely on synthetic examples.
2. **Real‑world inference** – The model runs on unseen real footage and produces candidate detections.
3. **Human review** – A human inspects each detection, but instead of labeling from scratch, they only zero out confirmed false positives—a far less effortful process.
4. **Fine‑tuning** – The refined dataset (real frames with corrected labels) is used to fine‑tune the model, and the loop repeats until performance converges.

This iterative approach progressively reduces false positives while preserving detection sensitivity. Because the correction step is unary (only false positives are marked), the human effort remains minimal even as the dataset grows.

```mermaid
flowchart LR
    A[Synthetic Training] --> B[Inference on Real Footage]
    B --> C[Manual Review & Zeroing]
    C --> D[Fine‑tune Model]
    D --> B
```

The resulting system integrates into Netflix’s broader content pipeline, catching pixel defects before they reach subscribers. Future work will likely extend the technique to other temporal artifacts and further reduce the human annotation burden through active learning.