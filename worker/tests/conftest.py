"""Test configuration.

The application builds its settings at import time and requires a few of them
(no defaults). Fake values are provided as environment defaults here, before any
`app` module is imported, so the test suite runs without a `.env` file. Values
already set in the environment take precedence.
"""

import os

_FAKE_ENV = {
    "MONGODB_CONNECTION_STR": "mongodb://fake-mongo.example.com:27017/fake",
    "BLOB_STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net",
    "CHAT_MODEL_BASE_URL": "https://fake-chat.example.com",
    "CHAT_MODEL_API_KEY": "fake-chat-key",
    "AZURE_AI_SEARCH_ENDPOINT": "https://fake-search.example.com",
    "AZURE_AI_SEARCH_API_KEY": "fake-search-key",
    "EMBEDDING_MODEL_ENDPOINT": "https://fake-embedding.example.com",
    "EMBEDDING_MODEL_API_KEY": "fake-embedding-key",
}

for _key, _value in _FAKE_ENV.items():
    os.environ.setdefault(_key, _value)
