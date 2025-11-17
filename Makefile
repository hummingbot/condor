.ONESHELL:
.PHONY: uninstall
.PHONY: install


uninstall:
	conda env remove -n condor

install:
	conda env create -f environment.yml
