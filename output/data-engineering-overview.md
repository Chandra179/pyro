# Netflix Data Engineering Overview

This document captures the breadth and depth of Netflix's data engineering practice, as articulated during the company's first Data Engineering Summit. The summit brought together engineers from across the organization to share internal best practices on building and operating data pipelines at massive scale. While the talks span a wide range of topics, together they illuminate a coherent set of principles, technologies, and innovations that underpin Netflix's data platform.

## Pipeline Architecture: Batch and Streaming

Netflix's data pipeline construction rests on a hybrid foundation that supports both batch and streaming workloads. Engineers from the Consolidated Logging team described how they compose pipelines using a common set of building blocks, leveraging the company's data stack to handle diverse data sources and consumption patterns. The architecture emphasizes flexibility—the same underlying components can be assembled for near-real-time streams or for large, scheduled batch jobs—allowing teams to choose the right paradigm for each use case without reinventing core infrastructure.

## Batch Processing Strategies

For batch workloads, Netflix relies on generic abstractions that abstract away the complexities of scaling, efficiency, and fault tolerance. These abstractions are designed to handle late-arriving data gracefully, a common challenge in event-driven systems where upstream delays can cause incomplete batches. By decoupling the processing logic from the execution engine, teams can improve resource utilization and adapt to changing data volumes without code rewrites. The strategies promote a "write once, scale as needed" mindset, reducing operational burden while maintaining high reliability.

## Streaming SQL with Apache Flink

A notable innovation is Netflix's managed streaming SQL offering built on Apache Flink, delivered as part of the company's Data Mesh stream processing platform. This service provides a SQL interface over Flink, enabling teams to express complex stream processing logic without deep expertise in the underlying framework. It has opened up new use cases—such as real-time analytics and event-driven applications—by lowering the barrier to entry and abstracting away operational concerns like state management, checkpoints, and exactly-once semantics.

## Reliable Data Pipeline Practices

Reliability is a central theme across all Netflix data engineering initiatives. Practitioners shared concrete practices for testing, validation, and auditing pipelines, using Apache Spark as a worked example but emphasizing that the principles are generalizable. These practices include data quality checks at each stage, contract testing for schema evolution, and automated alerts that trigger on anomalies. The goal is to catch issues before they impact downstream consumers, and to make pipelines self-diagnosing when failures do occur.

## Knowledge Management with Language Modeling

Beyond traditional data processing, Netflix applies data engineering techniques to internal knowledge management. An internal project leverages language modeling on metadata and a corpus of over 100,000 internal memos to improve searchability, discoverability, and overall impact. By treating memos as a data source, engineers can extract insights and build tools that help employees find relevant information quickly, thereby enhancing productivity and cross-team collaboration.

## Psyberg: Incremental ETL Framework

One of the most impactful presentations introduced Psyberg, an incremental ETL framework developed by the Membership Data Engineering team. Psyberg leverages Apache Iceberg metadata to efficiently detect new and changed data, enabling incremental processing without full table scans. This approach significantly reduces compute cost and latency, while also simplifying on-call operations because pipelines become more predictable and easier to reason about. The framework elegantly handles late-arriving data by using Iceberg's snapshot isolation, ensuring that reprocessing is always precise and scoped.

## ETL Optimization Case Study

A case study on optimizing complex ETL jobs provided concrete lessons for performance tuning. The presented approach involved profiling job stages, identifying bottlenecks, and applying targeted optimizations such as partitioning strategies, predicate pushdown, and careful management of shuffles. The results demonstrated order-of-magnitude improvements in runtime and resource consumption, underscoring the value of systematic analysis over ad‑hoc tweaks.

## Data in Content Production

Finally, the summit highlighted the role of data in content production—using insights from streaming behavior, viewer engagement, and content metadata to inform decisions about which movies and TV shows are made. This application of data engineering extends beyond infrastructure into the creative realm, embedding data as a core input to the content lifecycle.

## Key Technologies and Systems

The technologies that consistently appear throughout these discussions form the backbone of Netflix's data platform:

- **Apache Spark** – the primary engine for large‑scale batch processing, with a rich ecosystem of libraries for SQL, machine learning, and graph processing.
- **Apache Flink** – the engine for stream processing, powering the managed streaming SQL service on Data Mesh.
- **Apache Iceberg** – a table format for huge analytical datasets, providing snapshot isolation, schema evolution, and time travel, which enables frameworks like Psyberg to work efficiently.
- **Data Mesh** – Netflix's internal stream processing platform that hosts Flink and other streaming components, providing governance and operational consistency.
- **Psyberg** – an internal incremental ETL framework using Iceberg metadata to optimize pipelines and reduce operational complexity.

## Conclusion

Netflix's data engineering practice is characterized by a pragmatic blend of proven open‑source technologies and bespoke internal tools. The summit showcased a culture of sharing and continuous improvement, with techniques that are both advanced and applicable at scale. This document serves as a living snapshot of that practice, to be refined as new insights emerge from future engineering efforts.