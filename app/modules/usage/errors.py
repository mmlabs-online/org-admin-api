from app.core.errors import ConflictError


class UsageMetricExistsError(ConflictError):
    code = "usage_metric_exists"
