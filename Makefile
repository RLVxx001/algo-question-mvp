.PHONY: run test compile smoke

run:
	python3 -m app.server

test:
	python3 -m unittest discover -s tests -p 'test_*.py'

compile:
	python3 -m py_compile app/*.py tests/*.py scripts/*.py

smoke:
	python3 -m scripts.smoke
