.PHONY: build-AuthLayer

# Resolve the Python interpreter. Defaults to python3, but CI overrides this
# with a concrete path (e.g. `pyenv which python3`) to avoid pyenv shim issues
# inside SAM's CustomMakeBuilder subprocess.
PYTHON ?= python3

build-AuthLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	cp *.py "$(ARTIFACTS_DIR)/python"
	$(PYTHON) -m pip install -r requirements.txt -t "$(ARTIFACTS_DIR)/python"
