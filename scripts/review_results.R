# review_results.R -------------------------------------------------------
#
# Walk through a classification run by hand, section by section.
# Each block between the "## ---- n." markers stands on its own: run it,
# look at what it prints, then move on. Nothing is written to disk unless
# you run section 9 yourself.
#
# The normalisation and metric definitions mirror scripts/metrics.py so the
# numbers here are comparable with the Python pipeline and with the published
# Gemini run. If you change one, change the other.
#
# Point RESULTS at the run you want to inspect, then source the file or step
# through it in RStudio.

suppressPackageStartupMessages({
  library(jsonlite)
  library(dplyr)
  library(tidyr)
})

RESULTS  <- "results/classification/Results_Claude_Sonnet_5_5_Analysis/pilot_100_sonnet.jsonl"
CATEGORY <- "data/classification/phenotype_categories.tsv"
BASELINE <- "results/classification/Results_Gemini_3_8_Flash_Analysis/gemini_flash_full.jsonl"

FIVE_CLASS <- c("Pathogenic", "Likely Pathogenic", "Uncertain Significance",
                "Likely Benign", "Benign")
THREE_CLASS <- c("pathogenic", "vus", "benign")


## ---- 1. Load the run ---------------------------------------------------
# stream_in handles JSONL. Rows with an `error` field never got a
# classification; they are counted here and dropped in section 2.

read_run <- function(path) {
  stopifnot(file.exists(path))
  df <- jsonlite::stream_in(file(path), verbose = FALSE)
  # List columns (aiva_criteria, aiva_evidence) survive; we only touch scalars.
  df$error <- if ("error" %in% names(df)) df$error else NA_character_
  df
}

run <- read_run(RESULTS)

cat(sprintf("file     : %s\n", RESULTS))
cat(sprintf("records  : %d\n", nrow(run)))
cat(sprintf("errors   : %d\n", sum(!is.na(run$error))))
cat(sprintf("models   : %s\n", paste(unique(run$model_used), collapse = ", ")))
if ("execution_time_ms" %in% names(run)) {
  t <- run$execution_time_ms[!is.na(run$execution_time_ms)]
  if (length(t)) {
    cat(sprintf("latency s: min %.0f  median %.0f  max %.0f\n",
                min(t) / 1000, median(t) / 1000, max(t) / 1000))
  }
}


## ---- 2. Normalise the labels -------------------------------------------
# Mirrors normalize_classification() in scripts/metrics.py. Order matters:
# VUS is checked first because "pathogenic" is a substring of
# "VUS (leaning pathogenic)", and the compound labels are checked before the
# simple ones for the same reason.

normalize_classification <- function(x) {
  out <- trimws(ifelse(is.na(x), "", as.character(x)))
  lower <- tolower(out)
  ifelse(out == "", NA_character_,
  ifelse(startsWith(lower, "vus") | grepl("uncertain significance", lower, fixed = TRUE),
         "Uncertain Significance",
  ifelse(grepl("likely pathogenic", lower, fixed = TRUE), "Likely Pathogenic",
  ifelse(grepl("likely benign", lower, fixed = TRUE), "Likely Benign",
  ifelse(grepl("pathogenic", lower, fixed = TRUE), "Pathogenic",
  ifelse(grepl("benign", lower, fixed = TRUE), "Benign",
         out))))))
}

to_direction <- function(x) {
  dplyr::recode(x,
    "Pathogenic" = "pathogenic", "Likely Pathogenic" = "pathogenic",
    "Uncertain Significance" = "vus",
    "Likely Benign" = "benign", "Benign" = "benign",
    .default = NA_character_)
}

scored <- run %>%
  filter(is.na(error), !is.na(aiva_classification)) %>%
  mutate(
    truth      = normalize_classification(ground_truth),
    pred       = normalize_classification(aiva_classification),
    truth_dir  = to_direction(truth),
    pred_dir   = to_direction(pred),
    exact_hit  = truth == pred,
    dir_hit    = truth_dir == pred_dir
  )

# Anything the normaliser did not recognise would miscount silently.
unmapped <- setdiff(unique(c(scored$truth, scored$pred)), FIVE_CLASS)
if (length(unmapped)) {
  warning("unmapped labels: ", paste(unmapped, collapse = " | "))
}

cat(sprintf("\nscored   : %d of %d\n", nrow(scored), nrow(run)))
cat(sprintf("exact    : %.1f%%\n", 100 * mean(scored$exact_hit)))
cat(sprintf("direction: %.1f%%\n", 100 * mean(scored$dir_hit)))


## ---- 3. Is the model hedging? ------------------------------------------
# The smoke run returned VUS for 8 of 10 variants, including 5 that were
# Pathogenic. This is the check for whether that was an artefact of those
# 10 rows or systematic. Look at the VUS column: if the model calls VUS on
# variants the panel called Pathogenic, the prompt is not forcing a commit
# and the F1 in section 5 will be near zero for reasons that have nothing
# to do with the model's knowledge.

cat("\npredicted label distribution\n")
print(table(scored$pred))

cat("\nwhat the model says, by what the panel said\n")
print(table(truth = factor(scored$truth, FIVE_CLASS),
            predicted = factor(scored$pred, FIVE_CLASS)))

vus_rate <- mean(scored$pred == "Uncertain Significance")
non_vus_truth <- scored %>% filter(truth != "Uncertain Significance")
cat(sprintf("\nVUS called on %.0f%% of all variants\n", 100 * vus_rate))
if (nrow(non_vus_truth)) {
  cat(sprintf("VUS called on %.0f%% of variants the panel did NOT call VUS\n",
              100 * mean(non_vus_truth$pred == "Uncertain Significance")))
}


## ---- 4. Confusion matrices ---------------------------------------------

confusion <- function(truth, pred, levels) {
  table(truth = factor(truth, levels), predicted = factor(pred, levels))
}

cm5 <- confusion(scored$truth, scored$pred, FIVE_CLASS)
cm3 <- confusion(scored$truth_dir, scored$pred_dir, THREE_CLASS)

cat("\n5-class\n"); print(cm5)
cat("\n3-class (directional)\n"); print(cm3)


## ---- 5. Per-class precision, recall, F1 --------------------------------
# One-vs-rest, same arithmetic as calculate_precision_recall_f1(). A class
# with no support and no predictions gets NA rather than a misleading 0.

prf <- function(truth, pred, levels) {
  do.call(rbind, lapply(levels, function(cl) {
    tp <- sum(truth == cl & pred == cl, na.rm = TRUE)
    fp <- sum(truth != cl & pred == cl, na.rm = TRUE)
    fn <- sum(truth == cl & pred != cl, na.rm = TRUE)
    p  <- if (tp + fp > 0) tp / (tp + fp) else 0
    r  <- if (tp + fn > 0) tp / (tp + fn) else 0
    f  <- if (p + r > 0) 2 * p * r / (p + r) else 0
    support <- tp + fn
    data.frame(class = cl, support = support,
               precision = if (support == 0 && tp + fp == 0) NA else round(p, 4),
               recall    = if (support == 0) NA else round(r, 4),
               f1        = if (support == 0 && tp + fp == 0) NA else round(f, 4))
  }))
}

per_class  <- prf(scored$truth, scored$pred, FIVE_CLASS)
per_direction <- prf(scored$truth_dir, scored$pred_dir, THREE_CLASS)

cat("\nper class (5)\n"); print(per_class, row.names = FALSE)
cat("\nper direction (3)\n"); print(per_direction, row.names = FALSE)

# Macro F1 averages only classes that actually occur, as metrics.py does.
macro <- function(tbl) mean(tbl$f1[tbl$support > 0], na.rm = TRUE)
cat(sprintf("\nmacro F1 5-class: %.4f\n", macro(per_class)))
cat(sprintf("macro F1 3-class: %.4f\n", macro(per_direction)))


## ---- 6. By disease category --------------------------------------------
# Joins the curated phenotype map. Categories with very few variants in a
# 100-case pilot will swing wildly; the n column is there so you can ignore
# the small ones rather than over-read them.

by_category <- NULL
if (file.exists(CATEGORY)) {
  cats <- read.delim(CATEGORY, stringsAsFactors = FALSE)[, c("variant_id", "disease_category")]
  by_category <- scored %>%
    left_join(cats, by = "variant_id") %>%
    mutate(disease_category = ifelse(is.na(disease_category), "Unknown", disease_category)) %>%
    group_by(disease_category) %>%
    summarise(n = n(),
              exact = round(mean(exact_hit), 3),
              directional = round(mean(dir_hit), 3),
              .groups = "drop") %>%
    arrange(desc(n))
  cat("\nby disease category\n"); print(as.data.frame(by_category), row.names = FALSE)
} else {
  cat("\n(category map not found, skipping section 6)\n")
}


## ---- 7. By gene --------------------------------------------------------

by_gene <- scored %>%
  group_by(gene) %>%
  summarise(n = n(), exact = round(mean(exact_hit), 3),
            directional = round(mean(dir_hit), 3), .groups = "drop") %>%
  arrange(desc(n), gene)

cat(sprintf("\ngenes covered: %d\n", nrow(by_gene)))
print(as.data.frame(head(by_gene, 20)), row.names = FALSE)


## ---- 8. Compare against the published Gemini run -----------------------
# Only the variants both runs classified, so the comparison is like for like.
# Skipped if the baseline file is not present.

if (file.exists(BASELINE)) {
  base <- read_run(BASELINE) %>%
    filter(is.na(error), !is.na(aiva_classification)) %>%
    mutate(truth = normalize_classification(ground_truth),
           pred  = normalize_classification(aiva_classification),
           exact_hit = truth == pred,
           dir_hit = to_direction(truth) == to_direction(pred)) %>%
    select(variant_id, base_exact = exact_hit, base_dir = dir_hit)

  both <- scored %>%
    select(variant_id, exact_hit, dir_hit) %>%
    inner_join(base, by = "variant_id")

  cat(sprintf("\noverlap with Gemini baseline: %d variants\n", nrow(both)))
  if (nrow(both)) {
    cat(sprintf("  exact       sonnet %.1f%%   gemini %.1f%%\n",
                100 * mean(both$exact_hit), 100 * mean(both$base_exact)))
    cat(sprintf("  directional sonnet %.1f%%   gemini %.1f%%\n",
                100 * mean(both$dir_hit), 100 * mean(both$base_dir)))
  }
} else {
  cat("\n(baseline not found, skipping section 8)\n")
}


## ---- 9. Write the tables out -------------------------------------------
# Run this only when you want the numbers on disk. Nothing above writes.

if (FALSE) {
  out <- "results/classification/Results_Claude_Sonnet_5_5_Analysis"
  dir.create(out, showWarnings = FALSE, recursive = TRUE)
  write.csv(per_class,    file.path(out, "per_class_f1.csv"), row.names = FALSE)
  write.csv(per_direction, file.path(out, "per_direction_f1.csv"), row.names = FALSE)
  if (!is.null(by_category)) {
    write.csv(by_category, file.path(out, "by_category.csv"), row.names = FALSE)
  }
  write.csv(by_gene,      file.path(out, "by_gene.csv"), row.names = FALSE)
  write.csv(as.data.frame.matrix(cm5), file.path(out, "confusion_5class.csv"))
  write.csv(as.data.frame.matrix(cm3), file.path(out, "confusion_3class.csv"))
  cat("wrote tables to", out, "\n")
}
