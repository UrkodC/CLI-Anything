# R CLI — Software-Specific SOP

## Software Overview

**R** is the standard open-source environment for statistical computing and
graphics. Used for data analysis, visualization, machine learning, and
bioinformatics across research and industry.

## Backend Engine

- **Executable**: `Rscript`
- **Batch mode**: `Rscript --no-save --no-restore script.R`
- **Scripting**: R language (`.R` files)
- **Bundled**: None (system install required)

## Key Capabilities (Headless)

| Operation | Method |
|-----------|--------|
| Data I/O | CSV, TSV, Excel, RDS, RDA, JSON (readr, readxl, jsonlite) |
| Data manipulation | dplyr (filter, mutate, group_by, summarize, join) |
| Statistical tests | t-test, ANOVA, chi-squared, Wilcoxon, correlation |
| Regression | Linear (lm), logistic (glm), nonlinear |
| Multivariate | PCA, clustering, factor analysis |
| Visualization | ggplot2 (scatter, bar, histogram, boxplot, heatmap, line, density, violin) |
| Machine learning | caret, randomForest, glmnet |
| Bioinformatics | Bioconductor packages |
| Batch processing | Rscript command-line execution |
| Report generation | R Markdown, knitr |

## CLI Architecture

### Command Groups

- **project**: JSON-based project files tracking datasets, analyses, and plots
- **data**: Add/remove/list dataset references, inspect structure
- **analysis**: Configure and run statistical analyses (tests, regression, multivariate)
- **plot**: Build ggplot2 visualizations with aesthetic mappings and customization
- **script**: Store and manage custom R scripts
- **export**: Render through Rscript subprocess with all analyses and plots applied
- **backend**: Direct R invocation (run expression, run script, version)
- **session**: Undo/redo with deep-copy snapshots

### Data Flow

```
User loads datasets → Adds analysis/transforms → Configures plots
    → CLI generates self-contained R script
    → Export sends script to Rscript subprocess
    → R executes and produces output (plots, data, JSON results)
```

## Native Format

Project state: `.r-cli.json` (JSON)
Scripts: R language (`.R`)

## Required Dependencies

- **R**: https://www.r-project.org/ (hard dependency, not optional)
- **R packages**: jsonlite, ggplot2
- **Python**: click, prompt-toolkit
