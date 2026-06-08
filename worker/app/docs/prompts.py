"""Documentation prompts.

This module defines the prompts used by the AI model.
"""

from langchain_core.prompts import ChatPromptTemplate

from app.core.language_enum import Language

_LANGUAGE_INSTRUCTIONS: dict[Language, str] = {
    Language.JAVA: """\
### Structure and Components
For Java:
- Classes defined
- Main methods
- Patterns used
- For _ServiceImpl_: Spring annotations (`@Service`, `@Transactional`, `@Resource`, etc.)

For JSP:
- Forms
- Available actions
- Displayed data

### Business Rules and Logic
List ALL business rules identified in this file:
- Detailed description of what each rule does
- Precise location (line number / method)
- Conditions and validations
- Error cases or thrown exceptions

For _ServiceImpl_:
- Clearly distinguish:
  - business rules
  - data access logic
  - orchestration logic
  - post-processing

### Dependencies and Integrations
For each dependency (import, Spring injection, direct call):
- Services / classes used
- Exact role of each dependency
- Manipulated data (DTO, entities, VO, IDs...)
- Interactions with other components

For _ServiceImpl_:
- Calls to external services (e.g. webservices)
- Calls to persistence layers (DAO, repositories, business services)
- Mappings between DTO and entities
- Call chains (orchestration)

### Transactions and Error Handling (ServiceImpl specific)
- Presence of `@Transactional` and impact on logic
- Exception handling (types, conditions, propagation)
- Business impact in case of error""",
}


def get_language_instructions(language: Language) -> str:
    """Return the language-specific instruction block for a prompt.

    Args:
        language(Language): The programming language.

    Returns:
        str: The instruction text tailored to `language`.

    Raises:
        ValueError: If `language` has no registered instructions.
    """
    try:
        return _LANGUAGE_INSTRUCTIONS[language]
    except KeyError:
        raise ValueError(
            f"No documentation instructions registered for language '{language.value}'"
        ) from None


FILE_DOCUMENTATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert in code analysis and retro-documentation. You MUST write your entire response in French.",
        ),
        (
            "human",
            """\
# File to analyze: {file_path}

## Source code

```{language}
{source_code}
```

## Abstract Syntax Tree (AST)

```json
{ast_json}
```

## Control Flow Graphs (CFG)

```json
{cfg_json}
```

## Data Flow Graphs (DFG)

```json
{dfg_json}
```

## Instructions

Generate a documentation report in Markdown for THIS FILE ONLY with the following sections:

### Overview
- Role and responsibility of the file

{language_instructions}

### Data Flow Analysis
Using the AST, CFG and DFG provided:
- Key control flow paths through the code
- Data dependencies between variables and methods
- Critical data transformations

Important:
- Be very precise
- Cite code excerpts when relevant
- Identify all business rules, validations, calls, mappings
- Stay factual based on the analyzed code and graphs

Generate the report now:\
""",
        ),
    ]
)
