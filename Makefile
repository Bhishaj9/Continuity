install:
	pip install -r requirements.txt

test:
	GOOGLE_API_KEY=dummy pytest tests/
