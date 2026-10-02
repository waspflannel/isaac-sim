class Item:
    def __init__(self, item_id, quality=70):
        self.id = item_id
        self.quality = quality
        self.repair_attempts = 0
        self.test_results = []
        self.status = "in_progress"
        self.completed_steps = []
