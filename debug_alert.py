import sqlite3

DB_FILE = "carryman.db"


def fetch_groups_by_prefix(cursor):
    cursor.execute(
        """
        SELECT DISTINCT shop_name, chat_id
        FROM os_groups
        WHERE shop_name LIKE 'Thiri%' OR shop_name LIKE 'Easy%'
        ORDER BY shop_name, chat_id
        """
    )
    return cursor.fetchall()


def fetch_routing_for_chat(cursor, chat_id):
    cursor.execute(
        """
        SELECT os_topic_id, alert_chat_id, alert_topic_id, department_name
        FROM routing_table
        WHERE os_chat_id=?
        ORDER BY os_topic_id
        """,
        (chat_id,),
    )
    return cursor.fetchall()


def main():
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        print("Searching os_groups with prefix: Thiri% / Easy%")
        print("=" * 70)

        groups = fetch_groups_by_prefix(c)
        if not groups:
            print("No groups found with prefix Thiri% or Easy%.")
            return

        print("Matched groups:")
        for shop_name, chat_id in groups:
            print(f"- shop_name: {shop_name} | chat_id: {chat_id}")

        print("\nRouting check by chat_id:")
        for shop_name, chat_id in groups:
            routes = fetch_routing_for_chat(c, chat_id)
            if not routes:
                print(f"- {chat_id} ({shop_name}) => NOT ROUTED")
            else:
                print(f"- {chat_id} ({shop_name}) => ROUTED ({len(routes)} rows)")
                for os_topic_id, alert_chat_id, alert_topic_id, dept in routes:
                    print(
                        f"    os_topic_id={os_topic_id}, "
                        f"alert_chat_id={alert_chat_id}, "
                        f"alert_topic_id={alert_topic_id}, dept={dept}"
                    )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
