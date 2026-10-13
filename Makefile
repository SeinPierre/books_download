PYTHON := python3
VENV := .venv
VENV_BIN := $(VENV)/bin
SCRIPT := dl_100_books.py

.PHONY: all venv install run clean

all: run

# Create the virtual environment
$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

venv: $(VENV)/bin/activate

# Install dependencies into the venv
install: venv requirements.txt
	$(VENV_BIN)/pip install --upgrade pip
	$(VENV_BIN)/pip install -r requirements.txt

# Run the program inside the venv (ensures deps are installed first)
run: install
	$(VENV_BIN)/python $(SCRIPT)

# Remove the virtual environment
clean:
	rm -rf $(VENV)
