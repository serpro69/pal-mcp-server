"""Provider configuration faults, distinct from unavailable credentials."""


class RegistryConfigurationError(ValueError):
    """A model registry could not be read or validated."""

    def __init__(self, path: str, category: str, detail: str):
        self.path = path
        self.category = category
        super().__init__(f"Model registry '{path}' ({category}): {detail}")
