# Retro-Doc Backend

## About

The Retro-Doc project focuses on extracting knowledge from an existing codebase. This folder contains the backend implementation of that effort.

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Notes](#notes)
- [Resources](#resources)

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.10.9
- [Docker](https://www.docker.com/) >=29.5.2

> [!tip]
> Don't have uv? Install it with any python version like so:
> ```zsh
> pip install uv==0.10.9
> ```

## Installation

Install dependencies with the following command:
```zsh
uv sync
```

Activate the virtual environment created by uv:
```zsh
source .venv/Scripts/activate
```

> [!note] 
> In case you don't want to activate the virtual environment, prefix each following command with `uv run`.

Finally, for development only, setup [prek](https://prek.j178.dev) to configure your Git Hooks:
```zsh
prek install
```

## Usage

Start the required services (Gotenberg) via Docker Compose:
```zsh
docker compose up -d
```

Then run the application:
```zsh
uvicorn app.main:app --host localhost --port 8000
```

Then open to `http://localhost:8000/docs` to access the integrated [Swagger](https://swagger.io/) (only available when `APP_DEBUG` is set to `True`).

To stop the Docker services when you're done:
```zsh
docker compose down
```

## Notes

### What's the project structure?

It follows the [zhanymkanov's opinionated best practices](https://github.com/zhanymkanov/fastapi-best-practices). It's very similar to a Django project in which each module or domain is kind of self-contained.

## Resources

- [FastAPI Best Practices](https://github.com/zhanymkanov/fastapi-best-practices)

