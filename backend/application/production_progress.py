"""Monthly manufacturing progress, independently of material consumption."""

from decimal import Decimal


def completion(plans: dict[str, str], actuals: dict[str, str]) -> dict[str, str]:
    return {
        period: format(Decimal(actuals[period]) / Decimal(plan) * 100, ".2f")
        for period, plan in plans.items()
        if plan and Decimal(plan) > 0 and actuals.get(period, "") != ""
    }
