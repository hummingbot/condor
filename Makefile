.ONESHELL:
.PHONY: uninstall
.PHONY: install


uninstall:
	conda env remove -n condor -y

install:
	conda env create -f environment.yml
