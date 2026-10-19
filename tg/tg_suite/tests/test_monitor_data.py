import os
import tempfile
import unittest

import database


class MonitorDataTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = database.DB_PATH
        self.db_path = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.db_path
        database.init_db()
        database.init_leads_table()
        database.init_monitor_tables()

    def tearDown(self):
        database.DB_PATH = self.previous_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_keyword_rule_crud(self):
        rule_id = database.save_keyword_rule({
            "rule_name": "求购规则",
            "keyword_value": "求购",
            "match_mode": "contains",
            "priority": 10,
        })
        rules = database.list_keyword_rules()
        self.assertEqual(rules[0]["id"], rule_id)
        self.assertTrue(database.update_keyword_rule(rule_id, {"keyword_value": "采购", "rule_name": "采购规则"}))
        self.assertEqual(database.list_keyword_rules()[0]["keyword_value"], "采购")
        self.assertTrue(database.delete_keyword_rule(rule_id))
        self.assertEqual(database.list_keyword_rules(), [])

    def test_archive_pagination_and_filter(self):
        for index in range(3):
            database.save_archived_message({
                "account_name": "worker",
                "chat_id": 100,
                "message_id": index + 1,
                "chat_title": "测试群",
                "sender_name": f"用户{index}",
                "text": "求购设备" if index < 2 else "普通消息",
                "matched_keyword": "求购" if index < 2 else None,
            })
        result = database.query_archived_messages(page=1, page_size=1, keyword="求购")
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 1)

    def test_bot_target_crud(self):
        target_id = database.save_bot_target({
            "name": "销售群",
            "bot_token": "token",
            "chat_id": "-1001",
        })
        self.assertEqual(database.list_bot_targets()[0]["id"], target_id)
        self.assertTrue(database.delete_bot_target(target_id))
        self.assertEqual(database.list_bot_targets(), [])


if __name__ == "__main__":
    unittest.main()
