"""Language enumeration.

This module contains the language-related enum, which defines the supported programming
languages for code analysis.
"""

from enum import Enum


class Language(Enum):
    JAVA = "java"


class LanguageExtension(Enum):
    JAVA = ".java"
