PY ?= python3
export PYTHONPATH := src

.PHONY: test sample report example
test:
	$(PY) -m unittest discover -s tests -v

sample:
	$(PY) -m costreport sample --out data/sample.csv

report: sample
	$(PY) -m costreport report --input data/sample.csv

example: sample
	$(PY) -m costreport report --input data/sample.csv --end-date 2026-11-15 --config config.example.toml --out docs/example-report.md
