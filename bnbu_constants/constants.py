PENDING = "Pending"
APPROVED = "Approved"
REJECTED = "Rejected"
ERROR = "Error"

RENTAL_PROPERY_STATUS_CHOICES = [
    (PENDING, "Pending"),
    (APPROVED, "Approved"),
    (REJECTED, "Rejected"),
    (ERROR, "Error"),
]


# Legacy .xls needs the xlrd engine, which is not installed; every .xls
# upload used to pass this check and then fail to parse.
VALID_FILE_EXTENSION = [".xlsx", ".csv"]
REQUIRED_COLS = ["Location", "Price", "Sq. ft.", "Ba", "Br", "Link"]
IMP_COLS = ["Ba", "Br", "Price", "Link"]

CURRENCY_USD = "usd"
