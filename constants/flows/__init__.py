from .book import BOOK_PLANNER
from .price import PRICE_PLANNER
from .reschedule import RESCHEDULE_PLANNER
from .cancel import CANCEL_PLANNER
from .eta import ETA_PLANNER
from .status import STATUS_PLANNER
from .other import OTHER_PLANNER

FLOWS = {
    "BOOK": BOOK_PLANNER,
    "PRICE": PRICE_PLANNER,
    "RESCHEDULE": RESCHEDULE_PLANNER,
    "CANCEL": CANCEL_PLANNER,
    "ETA": ETA_PLANNER,
    "STATUS": STATUS_PLANNER,
    "OTHER": OTHER_PLANNER,
}
