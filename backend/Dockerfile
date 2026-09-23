# Dynamic python image to use
ARG PYTHON_IMAGE

FROM ${PYTHON_IMAGE}

# Set the working directory inside the container
WORKDIR /app/

# Dynamic uv version to use
ARG UV_VERSION

# Install uv
RUN pip install --disable-pip-version-check --no-cache-dir --root-user-action=ignore -q "uv==${UV_VERSION}"

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Copy the dependency files
COPY pyproject.toml uv.lock .

# Install dependencies using uv
RUN uv sync --frozen --compile-bytecode --link-mode=copy --no-cache --no-dev -q

# Copy the application code
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

# Inform on which port the app will run
EXPOSE 8000

# Run the FastAPI application with Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "300", "--workers", "2"]