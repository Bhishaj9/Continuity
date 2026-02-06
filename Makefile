install:
	pip install -r requirements.txt

test:
	GOOGLE_API_KEY=dummy pytest tests/

docker-build:
	docker build -t continuity-app -f Dockerfile .
