class Item:
    def __init__(self, item_id):
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("item_id must be a non-empty string")
        self.id = item_id
        self.completed_steps = []
