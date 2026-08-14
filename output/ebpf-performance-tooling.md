# eBPF Performance Tooling

Netflix has increasingly adopted eBPF (extended Berkeley Packet Filter) to enable deep, low‑level observability and programmability across its infrastructure. While eBPF offers powerful hooks into the kernel with minimal overhead, the very act of running many eBPF programs can itself impose a cost on the system. Balancing the benefits of eBPF against the resulting CPU load requires constant measurement and tuning. Until recently, this optimization loop was manual and burdensome.

## The Problem: Measuring What Matters

To optimize an eBPF program, engineers need to know how much CPU it consumes, how frequently it runs, and how long each invocation takes. Gathering these metrics previously meant attaching separate instrumentation, parsing kernel tracepoints, or writing custom scripts — all of which added friction to the iterative process of benchmarking, refining, and verifying a program’s performance. The overhead of the measurement itself also risked distorting the very performance being measured.

## bpftop: A Real‑Time View with Minimal Footprint

bpftop is an open‑source (Apache 2.0) command‑line tool that addresses this challenge directly. It provides a dynamic, real‑time dashboard of all running eBPF programs, presenting three key metrics per program:

- **Average execution runtime** – how long each program runs, on average.
- **Events per second** – how frequently the program is triggered.
- **Estimated CPU percentage** – the proportion of a CPU core the program consumes.

The data is shown either as a top‑like table or as time‑series graphs over a rolling 10‑second window. The graphs are particularly useful for surfacing short‑lived spikes or cyclic behavior that a single snapshot from a table might miss.

## Design for Near‑Zero Overhead

The most important architectural decision in bpftop is how it collects these statistics without influencing the system it observes. The Linux kernel keeps eBPF runtime statistics disabled by default precisely because always‑on instrumentation would add overhead to every eBPF program. bpftop flips this trade‑off by using the `BPF_ENABLE_STATS` syscall to turn on statistics collection *only for its own lifetime*.

- When bpftop starts, it enables eBPF stats globally.
- It then samples each eBPF program’s counters once per second.
- It computes the averages and renders them in the terminal UI.
- On exit, it disables the stats again, leaving the kernel in its default low‑overhead state.

This toggling design keeps the measurement footprint to a minimum — the only cost is the few seconds bpftop is actually running, and even then the overhead is limited to the act of reading counters.

## Implementation Notes

bpftop is written in Rust, leveraging two key crates: `libbpf-rs` for interacting with eBPF objects and the kernel, and `ratatui` for building the terminal user interface. This choice of languages and libraries ensures both safety and expressiveness, while keeping the tool lightweight and portable across the kernels Netflix relies on.

## Architectural Insight

The core principle embodied by bpftop is that monitoring infrastructure should not become a permanent fixture. By enabling kernel‑level statistics only when needed, and disabling them immediately after, the tool avoids the classic pitfall of signal distortion and persistent overhead. This pattern — temporarily enabling a deeper level of instrumentation around a specific tool’s execution — is a useful general strategy for any performance‑sensitive environment where the act of measuring can alter the outcome.