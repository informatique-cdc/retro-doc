"""Language domain.

This module defines the supported programming languages for code analysis,
their file extensions, and helpers to map a file to its language.
"""

from enum import Enum


class Language(Enum):
    JAVA = "java"


EXTENSIONS_BY_LANGUAGE: dict[Language, tuple[str, ...]] = {
    Language.JAVA: (".java",),
}


def get_language_from_path(path: str) -> Language | None:
    """Detect the supported language of a file from its path extension.

    Args:
        path(str): The file path (or name) to inspect.

    Returns:
        Language | None: The matching supported language, or None when no
            supported extension matches.
    """
    for language, extensions in EXTENSIONS_BY_LANGUAGE.items():
        if path.endswith(extensions):
            return language
    return None


def get_supported_languages() -> list[Language]:
    """Get all languages the pipeline can analyze/filter by.

    Returns:
        list[Language]: The supported languages, in definition order.
    """
    return list(Language)
