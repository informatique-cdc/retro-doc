# To enable ssh & remote debugging on app service change the base image to the one below
# FROM mcr.microsoft.com/azure-functions/python:4-python3.12-appservice
FROM mcr.microsoft.com/azure-functions/python:4-python3.12

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# Cloning a git source repository shells out to git
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends -qq git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dynamic uv version to use
ARG UV_VERSION

# Using uv to manage dependencies (Azure use pip internally)
COPY pyproject.toml uv.lock /
RUN pip install -q "uv==${UV_VERSION}"
RUN python -m uv export --frozen --no-dev --format requirements.txt --output-file /requirements.txt --project / -q && python -m uv pip install --system -r /requirements.txt -q

WORKDIR /home/site/wwwroot
COPY . .

# Trust an extra CA bundle when the build context provides one (TLS-inspecting proxy)
RUN if [ -f ca-bundle.pem ]; then \
        mkdir -p /usr/local/share/ca-certificates && \
        cp ca-bundle.pem /usr/local/share/ca-certificates/extra-ca.crt && \
        update-ca-certificates && \
        rm -f ca-bundle.pem; \
    fi

# Make the ssl module (httpx) and requests read the system trust store
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt