
lint:
	pylint -j $(shell nproc) -f colorized -r n --rcfile=pylintrc ippchef

.PHONY: lint
