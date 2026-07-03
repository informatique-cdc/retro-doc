# Retro-Doc Worker

## About

The Retro-Doc project focuses on extracting knowledge from an existing codebase. This folder contains the worker implementation used by the backend.

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Notes](#notes)
- [Resources](#resources)

## Requirements

- [node](https://nodejs.org/en) 22.4.1
- [uv](https://docs.astral.sh/uv/) 0.10.9

> [!tip] 
> Don't have uv? Install it with any python version like so:
> ```zsh
> pip install uv==0.10.9
> ```

## Installation

### Node packages

Install Azure Functions Core Tools (`func` CLI) to be able to run a server locally:
```zsh
npm i -g azure-functions-core-tools@4 --unsafe-perm true
```

Configure it by creating a file in the root folder named `local.settings.json` that contains this JSON data:
```json
{
    "IsEncrypted": false,
    "Values": {
        "AzureWebJobsStorage": "UseDevelopmentStorage=true",
        "FUNCTIONS_WORKER_RUNTIME": "python"
    }
}
```

This file tells Azure Functions Core Tools to connect to Azurite, which emulates Azure Storage services locally.

Finally, install Azurite like so:
```zsh
npm i -g azurite
```

### Python packages

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

To run the application locally, run first Azurite:
```zsh
azurite --skipApiVersionCheck --location ./.azurite-data
```

Then in another terminal, run a local server with the following command:
```zsh
func start
```

> [!note] 
> That latter command needs to be run in the virtual environment.


## Resources

- [Azure Durable Function example](https://github.com/Azure-Samples/durable-functions-quickstart-python-azd)