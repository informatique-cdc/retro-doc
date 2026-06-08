# To enable ssh & remote debugging on app service change the base image to the one below
# FROM mcr.microsoft.com/azure-functions/python:4-python3.12-appservice
FROM mcr.microsoft.com/azure-functions/python:4-python3.12

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# Dynamic uv version to use
ARG UV_VERSION

# Using uv to manage dependencies (Azure use pip internally)
COPY pyproject.toml uv.lock /
RUN pip install -q "uv==${UV_VERSION}"
RUN python -m uv export --frozen --no-dev --format requirements.txt --output-file /requirements.txt --project / -q && python -m uv pip install --system -r /requirements.txt -q

COPY . /home/site/wwwroot