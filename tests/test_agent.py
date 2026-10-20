import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timezone

from agent import (
    SOP_SYNC,
    Intent,
    LiveLLMPlanner,
    OperationsAgent,
    OpsState,
    PlannerAction,
    classify_intent,
    handle_n8n_webhook,
    step_clock,
)
from mock_systems import ConnectionTimeoutError, MockSystems
from models import ExecutionStatus

START = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)


def approve(_name, _args, _reason):
    return True


def deny(_name, _args, _reason):
    return False


def agent(**kwargs):
    kwargs.setdefault("approval_callback", approve)
    kwargs.setdefault("clock", step_clock(START))
    return OperationsAgent(**kwargs)


PROPOSE_100245 = (
    "Zjisti, proč objednávka 100245 nebyla expedována, "
    "a pokud je problém v synchronizaci, navrhni další postup."
)
REPAIR_100245 = (
    "Prověř objednávku 100245. Pokud vázne synchronizace do skladu "
    "a objednávka je zaplacená, rovnou synchronizaci zopakuj."
)
PAYMENT_100246 = "Zjisti, proč objednávka 100246 nebyla expedována, a navrhni další postup."
OUTAGE_100500 = "Zákazník urguje objednávku 100500. Zjisti stav expedice a vyřeš případný problém."
MISSING = "Zjisti stav objednávky 999999 a navrhni další postup."


def tool_message(name, arguments, content=""):
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


def text_message(payload):
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class ScenarioTests(unittest.TestCase):
    def test_propose_does_not_mutate(self):
        runner = agent()
        report = runner.run(PROPOSE_100245)
        tools = [step.tool_called for step in report.audit_trail]
        self.assertEqual(report.final_status, ExecutionStatus.PROPOSED_ACTION)
        self.assertEqual(tools, ["get_order", "get_payment", "get_sync_logs", "get_fulfillment"])
        self.assertIn("ODOO_ERP_SYNC_TIMEOUT_504", report.root_cause_summary)
        self.assertEqual(report.sop_reference, SOP_SYNC)
        self.assertNotIn("504", report.customer_care_suggestion)
        self.assertIsNone(runner.session.env.db["100245"]["fulfillment"]["fulfillment_id"])
        order = report.audit_trail[0].tool_output["data"]
        self.assertEqual(order["shopify_order_gid"], "gid://shopify/Order/589100245")
        self.assertEqual(order["store"], "fabini.cz")
        self.assertEqual(order["items"][0]["sku"], "FAB-LIT-PEK-26")
        self.assertEqual(order["items"][0]["title"], "Litinový pekáč s poklicí 26 cm")
        self.assertIn("FABINI_GARANCE", order["tags"])

    def test_repair_creates_fulfillment_after_approval(self):
        seen = []

        def spy(name, args, reason):
            seen.append((name, args, reason))
            return True

        runner = agent(approval_callback=spy)
        report = runner.run(REPAIR_100245)
        self.assertEqual(report.final_status, ExecutionStatus.RESOLVED)
        retry = report.audit_trail[-1]
        self.assertEqual(retry.tool_called, "retry_sync")
        self.assertEqual(retry.approval_status.value, "HUMAN_APPROVED")
        self.assertEqual(retry.tool_output["fulfillment_id"], "FUL-998")
        self.assertEqual(runner.session.env.db["100245"]["fulfillment"]["fulfillment_id"], "FUL-998")
        self.assertIn("100245", runner.session.executed_retries)
        self.assertEqual(seen[0][0], "retry_sync")
        self.assertIn("PAID", seen[0][2])
        self.assertNotIn("FUL-998", report.customer_care_suggestion)

    def test_payment_hold_takes_no_risky_action(self):
        def boom(*_args):
            raise AssertionError("HITL se u nezaplacené objednávky volat nemá")

        report = agent(approval_callback=boom).run(PAYMENT_100246)
        self.assertEqual(report.final_status, ExecutionStatus.PROPOSED_ACTION)
        self.assertIn("UNPAID_AWAITING_BANK_TRANSFER", report.root_cause_summary)
        self.assertEqual(report.sop_reference, "SOP-FIN-02: Bank Transfer SLA 3 Days")
        self.assertTrue(all(step.tool_called.startswith("get_") for step in report.audit_trail))

    def test_wms_timeout_becomes_ticket(self):
        report = agent().run(OUTAGE_100500)
        fulfillment = next(step for step in report.audit_trail if step.tool_called == "get_fulfillment")
        ticket = report.audit_trail[-1]
        self.assertEqual(fulfillment.tool_output["error_type"], "ConnectionTimeoutError")
        self.assertIn("2/2", fulfillment.tool_output["details"])
        self.assertFalse(fulfillment.tool_output["retryable"])
        self.assertEqual(ticket.tool_called, "create_ticket")
        self.assertEqual(ticket.approval_status.value, "HUMAN_APPROVED")
        self.assertEqual(ticket.tool_output["ticket_id"], "INC-2026-0891")
        self.assertEqual(report.final_status, ExecutionStatus.ESCALATED)
        self.assertEqual(report.sop_reference, "SOP-IT-09: Odoo/WMS 503 L2 Escalation")
        for banned in ("503", "504", "Odoo", "n8n", "timeout"):
            self.assertNotIn(banned, report.customer_care_suggestion)

    def test_missing_order_stops(self):
        report = agent().run(MISSING)
        self.assertEqual(report.final_status, ExecutionStatus.FAILED)
        self.assertEqual([step.tool_called for step in report.audit_trail], ["get_order"])
        self.assertEqual(report.audit_trail[0].tool_output["code"], 404)
        self.assertEqual(report.audit_trail[0].tool_output["message"], "Objednávka 999999 neexistuje.")

    def test_runs_do_not_share_a_database(self):
        repair = agent()
        repair.run(REPAIR_100245)
        other = MockSystems()
        self.assertEqual(repair.session.env.db["100245"]["fulfillment"]["fulfillment_id"], "FUL-998")
        self.assertIsNone(other.db["100245"]["fulfillment"]["fulfillment_id"])
        propose = agent()
        propose.run(PROPOSE_100245)
        self.assertIsNone(propose.session.env.db["100245"]["fulfillment"]["fulfillment_id"])


class GuardrailTests(unittest.TestCase):
    def test_duplicate_call_is_not_executed_twice(self):
        def loop(state):
            if sum(step.tool_called == "get_order" for step in state.trail) < 2:
                return PlannerAction("znovu", tool_name="get_order", tool_args={"order_id": "100245"})
            return PlannerAction("stačí", finish=True)

        report = agent().run(PROPOSE_100245, planner=loop)
        self.assertEqual(report.audit_trail[1].error, "DUPLICATE_CALL_BLOCKED")
        self.assertEqual(report.audit_trail[1].approval_status.value, "BLOCKED_BY_GUARDRAIL")

    def test_step_cap_without_a_diagnosis_escalates(self):
        def loop(_state):
            return PlannerAction("pořád dokola", tool_name="get_sync_logs", tool_args={"order_id": "100245"})

        report = agent(max_steps=6).run(PROPOSE_100245, planner=loop)
        self.assertEqual(len(report.audit_trail), 6)
        self.assertEqual(report.final_status, ExecutionStatus.ESCALATED)
        self.assertEqual(report.audit_trail[1].error, "DUPLICATE_CALL_BLOCKED")

    def test_step_cap_keeps_a_finished_diagnosis(self):
        report = agent(max_steps=4).run(PROPOSE_100245)
        self.assertEqual(len(report.audit_trail), 4)
        self.assertEqual(report.final_status, ExecutionStatus.PROPOSED_ACTION)
        self.assertIn("ODOO_ERP_SYNC_TIMEOUT_504", report.root_cause_summary)
        self.assertEqual(report.sop_reference, SOP_SYNC)

    def test_propose_mode_blocks_mutation_before_hitl(self):
        def force(_state):
            return PlannerAction("chci to stejně spustit", tool_name="retry_sync", tool_args={"order_id": "100245"})

        def boom(*_args):
            raise AssertionError("schvalování se nemá otevřít")

        runner = agent(max_steps=1, approval_callback=boom)
        report = runner.run(PROPOSE_100245, planner=force)
        self.assertEqual(report.audit_trail[0].error, "POLICY_PROPOSE_ONLY")
        self.assertIsNone(runner.session.env.db["100245"]["fulfillment"]["fulfillment_id"])

    def test_explicit_propose_overrides_repair_wording(self):
        report = agent().run(REPAIR_100245, intent="propose")
        self.assertNotIn("retry_sync", [step.tool_called for step in report.audit_trail])
        self.assertEqual(report.final_status, ExecutionStatus.PROPOSED_ACTION)

    def test_unpaid_retry_is_blocked_even_if_asked(self):
        def force(state):
            if "get_payment" not in state.tried:
                return PlannerAction("platba", tool_name="get_payment", tool_args={"order_id": "100246"})
            if not any(step.tool_called == "retry_sync" for step in state.trail):
                return PlannerAction("zkusím to", tool_name="retry_sync", tool_args={"order_id": "100246"})
            return PlannerAction("konec", finish=True)

        runner = agent()
        report = runner.run("U objednávky 100246 zopakuj synchronizaci do skladu.", planner=force)
        blocked = report.audit_trail[-1]
        self.assertEqual(blocked.error, "BUSINESS_RULE")
        self.assertIn("UNPAID_AWAITING_BANK_TRANSFER", blocked.tool_output["message"])
        self.assertIsNone(runner.session.env.db["100246"]["fulfillment"]["fulfillment_id"])

    def test_retry_once_per_order(self):
        def twice(state):
            if "get_payment" not in state.tried:
                return PlannerAction("platba", tool_name="get_payment", tool_args={"order_id": "100245"})
            retries = [step for step in state.trail if step.tool_called == "retry_sync"]
            if not retries:
                return PlannerAction("poprvé", tool_name="retry_sync", tool_args={"order_id": "100245"})
            if len(retries) == 1:
                return PlannerAction(
                    "podruhé",
                    tool_name="retry_sync",
                    tool_args={"order_id": "100245", "note": "again"},
                )
            return PlannerAction("konec", finish=True)

        runner = agent()
        report = runner.run(REPAIR_100245, planner=twice)
        self.assertEqual(report.audit_trail[-1].error, "RETRY_LIMIT")
        self.assertEqual(runner.session.env.db["100245"]["fulfillment"]["fulfillment_id"], "FUL-998")

    def test_infrastructure_failure_is_not_a_successful_retry(self):
        env = MockSystems(retry_fails={"100245"})
        runner = agent()
        report = runner.run(REPAIR_100245, environment=env)
        self.assertIn("100245", runner.session.failed_retry_attempts)
        self.assertNotIn("100245", runner.session.executed_retries)
        self.assertEqual(report.audit_trail[-1].tool_called, "create_ticket")
        self.assertEqual(report.final_status, ExecutionStatus.ESCALATED)
        self.assertEqual(report.sop_reference, "SOP-IT-09: Odoo/WMS 503 L2 Escalation")

    def test_rejection_does_not_change_warehouse(self):
        runner = agent(approval_callback=deny)
        report = runner.run(REPAIR_100245)
        retry = next(step for step in report.audit_trail if step.tool_called == "retry_sync")
        self.assertEqual(retry.approval_status.value, "HUMAN_REJECTED")
        self.assertEqual(report.final_status, ExecutionStatus.PROPOSED_ACTION)
        self.assertIsNone(runner.session.env.db["100245"]["fulfillment"]["fulfillment_id"])

    def test_deferred_approval_stops_the_loop(self):
        runner = agent(approval_callback=lambda *_args: None)
        report = runner.run(REPAIR_100245)
        self.assertEqual(report.final_status, ExecutionStatus.AWAITING_APPROVAL)
        self.assertEqual(report.audit_trail[-1].approval_status.value, "AWAITING_APPROVAL")
        self.assertIsNone(runner.session.env.db["100245"]["fulfillment"]["fulfillment_id"])

    def test_wms_failure_does_not_crash_the_process(self):
        report = agent(approval_callback=deny).run(OUTAGE_100500)
        self.assertEqual(report.final_status, ExecutionStatus.ESCALATED)
        self.assertTrue(any(step.error for step in report.audit_trail))

    def test_clock_is_stable(self):
        first = agent().run(MISSING).model_dump()
        second = agent().run(MISSING).model_dump()
        self.assertEqual(first, second)
        self.assertEqual(first["audit_trail"][0]["timestamp"], "2026-10-14T08:00:00+00:00")


class ToolTests(unittest.TestCase):
    def test_fulfillment_retries_once_then_raises(self):
        with self.assertRaises(ConnectionTimeoutError) as caught:
            MockSystems().get_fulfillment("100500")
        self.assertIn("2/2", str(caught.exception))
        self.assertIn("503", str(caught.exception))

    def test_retry_sync_creates_ful_998(self):
        env = MockSystems()
        result = env.retry_sync("100245")
        self.assertEqual(result["fulfillment_id"], "FUL-998")
        self.assertEqual(env.db["100245"]["fulfillment"]["status"], "QUEUED_FOR_PACKING")

    def test_parallel_environments_do_not_share_mutations(self):
        left = MockSystems()
        right = MockSystems()
        left.retry_sync("100245")
        self.assertEqual(left.db["100245"]["fulfillment"]["fulfillment_id"], "FUL-998")
        self.assertIsNone(right.db["100245"]["fulfillment"]["fulfillment_id"])

    def test_not_found_messages_are_czech(self):
        env = MockSystems()
        messages = {
            "order": env.get_order("999999")["message"],
            "payment": env.get_payment("999999")["message"],
            "fulfillment": env.get_fulfillment("999999")["message"],
            "logs": env.get_sync_logs("999999")["message"],
            "retry": env.retry_sync("999999")["message"],
        }
        self.assertEqual(messages["order"], "Objednávka 999999 neexistuje.")
        self.assertEqual(messages["payment"], "Platba pro objednávku 999999 nenalezena.")
        self.assertEqual(messages["fulfillment"], "Expedice pro objednávku 999999 nenalezena.")
        self.assertEqual(messages["logs"], "Logy pro objednávku 999999 nenalezeny.")
        self.assertEqual(messages["retry"], "Objednávka 999999 nenalezena.")
        for message in messages.values():
            self.assertNotIn("Objednávka pro objednávku", message)
            self.assertFalse(message.endswith("nenalezeno."))

    def test_sync_log_is_the_n8n_odoo_timeout(self):
        logs = MockSystems().get_sync_logs("100245")["data"]
        self.assertEqual(logs["error_code"], "ODOO_ERP_SYNC_TIMEOUT_504")
        self.assertEqual(logs["channel"], "n8n")
        self.assertEqual(logs["flow"], "shopify-order-paid-to-odoo")
        self.assertEqual(MockSystems().get_order("100246")["data"]["store"], "fabini.sk")

    def test_intent(self):
        self.assertEqual(classify_intent(PROPOSE_100245), Intent.PROPOSE)
        self.assertEqual(classify_intent(REPAIR_100245), Intent.EXECUTE)
        self.assertEqual(classify_intent(OUTAGE_100500), Intent.EXECUTE)
        self.assertEqual(classify_intent("zařiď to prosím"), Intent.EXECUTE)
        self.assertEqual(classify_intent("zkus znovu"), Intent.EXECUTE)
        self.assertEqual(classify_intent("vytvoř ticket na sklad"), Intent.EXECUTE)
        self.assertEqual(classify_intent("vyres to"), Intent.EXECUTE)


class LiveAndWebhookTests(unittest.TestCase):
    def test_from_env_picks_a_provider(self):
        self.assertIsNone(LiveLLMPlanner.from_env({}))
        gemini = LiveLLMPlanner.from_env({"GEMINI_API_KEY": "g"})
        self.assertEqual(gemini.model, "gemini-2.5-flash")
        self.assertEqual(gemini.base_url, "https://generativelanguage.googleapis.com/v1beta/openai")
        openai = LiveLLMPlanner.from_env({"OPENAI_API_KEY": "sk"})
        self.assertEqual(openai.model, "gpt-4o-mini")
        self.assertEqual(openai.base_url, "https://api.openai.com/v1")
        xai = LiveLLMPlanner.from_env({"XAI_API_KEY": "x", "OPENAI_API_KEY": "sk"})
        self.assertEqual(xai.model, "grok-3-mini")
        self.assertEqual(xai.base_url, "https://api.x.ai/v1")

    def test_live_retry_on_a_proposal_never_reaches_hitl(self):
        calls = []

        def http_post(payload):
            calls.append(payload)
            if len(calls) == 1:
                return tool_message("retry_sync", {"order_id": "100245"}, "zkusím retry")
            return text_message(
                {
                    "status": "RESOLVED",
                    "root_cause": "model si vymyslel hotovo",
                    "recommended_next_steps": ["nic"],
                    "summary_for_operator": "nic",
                    "sop_reference": "",
                    "customer_care_suggestion": "Hotovo.",
                }
            )

        def boom(*_args):
            raise AssertionError("HITL")

        planner = LiveLLMPlanner("k", "https://example.invalid/v1", "grok-3-mini", http_post=http_post)
        report = agent(max_steps=3, approval_callback=boom).run(PROPOSE_100245, planner=planner)
        self.assertEqual(calls[0]["tool_choice"], "auto")
        names = {item["function"]["name"] for item in calls[0]["tools"]}
        self.assertIn("retry_sync", names)
        self.assertEqual(report.audit_trail[0].error, "POLICY_PROPOSE_ONLY")
        self.assertNotEqual(report.final_status, ExecutionStatus.RESOLVED)

    def test_live_404_overrides_a_resolved_claim(self):
        calls = {"n": 0}

        def http_post(_payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return tool_message("get_order", {"order_id": "999999"})
            return text_message(
                {
                    "status": "RESOLVED",
                    "root_cause": "všechno v pořádku",
                    "recommended_next_steps": ["nic"],
                    "summary_for_operator": "nic",
                    "sop_reference": "SOP-X",
                    "customer_care_suggestion": "Hotovo, zboží je na cestě.",
                }
            )

        planner = LiveLLMPlanner("k", "https://example.invalid/v1", "grok-3-mini", http_post=http_post)
        report = agent().run(MISSING, planner=planner)
        self.assertEqual(report.final_status, ExecutionStatus.FAILED)
        self.assertIn("neexistuje", report.root_cause_summary)

    def test_n8n_webhook_defers_and_approves(self):
        waiting = handle_n8n_webhook(
            {"prompt": REPAIR_100245, "intent": "execute", "operator_approved": None},
            clock=step_clock(START),
        )
        self.assertEqual(waiting["report"]["final_status"], "AWAITING_APPROVAL")
        self.assertEqual(waiting["approval_webhook_payload"]["proposed_action"]["tool"], "retry_sync")
        self.assertEqual(waiting["approval_webhook_payload"]["buttons"], ["approve", "reject"])

        done = handle_n8n_webhook(
            {"prompt": REPAIR_100245, "intent": "execute", "operator_approved": True},
            clock=step_clock(START),
        )
        self.assertEqual(done["report"]["final_status"], "RESOLVED")
        self.assertIsNone(done["approval_webhook_payload"])
        self.assertNotIn("504", done["report"]["customer_care_suggestion"])


class WorkerStateTests(unittest.TestCase):
    def test_second_webhook_does_not_write_odoo_again(self):
        with tempfile.TemporaryDirectory() as directory:
            ops = OpsState(directory)
            first_env = MockSystems()
            second_env = MockSystems()
            payload = {"prompt": REPAIR_100245, "intent": "execute", "operator_approved": True}
            first = handle_n8n_webhook(payload, clock=step_clock(START), ops=ops, environment=first_env)
            second = handle_n8n_webhook(payload, clock=step_clock(START), ops=ops, environment=second_env)

        self.assertEqual(first["report"]["final_status"], "RESOLVED")
        self.assertEqual(second["report"]["final_status"], "RESOLVED")
        self.assertEqual(first_env.db["100245"]["fulfillment"]["fulfillment_id"], "FUL-998")
        self.assertEqual(second_env.db["100245"]["fulfillment"]["status"], "NOT_FOUND")
        replay = [step for step in second["report"]["audit_trail"] if step["tool_called"] == "retry_sync"]
        self.assertEqual(replay[0]["tool_output"]["idempotent_replay"], True)
        self.assertEqual(replay[0]["tool_output"]["fulfillment_id"], "FUL-998")
        self.assertIn("gid://shopify/Order/589100245|2026-10-14|retry_sync", ops._data["writes"])

    def test_approval_id_finishes_the_same_action_once(self):
        with tempfile.TemporaryDirectory() as directory:
            ops = OpsState(directory)
            waiting = handle_n8n_webhook(
                {"prompt": REPAIR_100245, "intent": "execute", "operator_approved": None},
                clock=step_clock(START),
                ops=ops,
            )
            approval_id = waiting["approval_webhook_payload"]["approval_id"]
            self.assertEqual(
                waiting["approval_webhook_payload"]["resume"],
                {"approval_id": approval_id, "operator_approved": True},
            )
            writer = MockSystems()
            done = handle_n8n_webhook(
                {"approval_id": approval_id, "operator_approved": True},
                clock=step_clock(START),
                ops=ops,
                environment=writer,
            )
            untouched = MockSystems()
            again = handle_n8n_webhook(
                {"approval_id": approval_id, "operator_approved": True},
                clock=step_clock(START),
                ops=ops,
                environment=untouched,
            )

        self.assertEqual(done["report"]["final_status"], "RESOLVED")
        self.assertEqual(again, done)
        self.assertEqual(writer.db["100245"]["fulfillment"]["fulfillment_id"], "FUL-998")
        self.assertEqual(untouched.db["100245"]["fulfillment"]["status"], "NOT_FOUND")
        self.assertEqual(ops.pending(), [])

    def test_unknown_approval_is_a_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            body = handle_n8n_webhook(
                {"approval_id": "missing", "operator_approved": True},
                clock=step_clock(START),
                ops=OpsState(directory),
            )
        self.assertEqual(body["report"]["final_status"], "FAILED")
        self.assertIn("neexistuje", body["report"]["root_cause_summary"])

    def test_http_server_defers_and_lists_the_run(self):
        from main import build_server

        with tempfile.TemporaryDirectory() as directory:
            ops = OpsState(directory)
            httpd = build_server("127.0.0.1", 0, ops, clock=step_clock(START))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            host, port = httpd.server_address[:2]
            base = f"http://{host}:{port}"
            try:
                with urllib.request.urlopen(base + "/health") as response:
                    health = json.loads(response.read().decode())
                request = urllib.request.Request(
                    base + "/hooks/n8n",
                    data=json.dumps(
                        {"prompt": REPAIR_100245, "intent": "execute", "operator_approved": None}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request) as response:
                    posted = json.loads(response.read().decode())
                with urllib.request.urlopen(base + "/approvals") as response:
                    approvals = json.loads(response.read().decode())
                with urllib.request.urlopen(base + "/runs") as response:
                    runs = json.loads(response.read().decode())
            finally:
                httpd.shutdown()
                httpd.server_close()

        self.assertEqual(health["status"], "ok")
        self.assertEqual(posted["report"]["final_status"], "AWAITING_APPROVAL")
        self.assertEqual(approvals["approvals"][0]["tool"], "retry_sync")
        self.assertEqual(runs["runs"][0]["order_id"], "100245")
        self.assertEqual(runs["runs"][0]["final_status"], "AWAITING_APPROVAL")


class BugFixRegressionTests(unittest.TestCase):
    def test_ticket_rejection_blocks_subsequent_attempts_even_if_reworded(self):
        asked = []

        def deny_once(name, args, reason):
            asked.append((name, dict(args)))
            return False

        steps = iter(
            [
                PlannerAction("čti", tool_name="get_order", tool_args={"order_id": "100500"}),
                PlannerAction("plať", tool_name="get_payment", tool_args={"order_id": "100500"}),
                PlannerAction("sync", tool_name="get_sync_logs", tool_args={"order_id": "100500"}),
                PlannerAction("wms", tool_name="get_fulfillment", tool_args={"order_id": "100500"}),
                PlannerAction("ticket1", tool_name="create_ticket", tool_args={"description": "Výpadek WMS 100500"}),
                PlannerAction("ticket2", tool_name="create_ticket", tool_args={"description": "Jiný popis pro 100500"}),
            ]
        )

        def attack(_state):
            try:
                return next(steps)
            except StopIteration:
                return PlannerAction("dost", finish=True)

        runner = agent(approval_callback=deny_once, max_steps=6)
        report = runner.run("Vyřeš problém s objednávkou 100500", planner=attack)
        self.assertEqual(len(asked), 1)
        self.assertEqual(report.audit_trail[-2].approval_status.value, "HUMAN_REJECTED")
        self.assertEqual(report.audit_trail[-1].error, "DUPLICATE_CALL_BLOCKED")

    def test_ticket_cross_order_scope_is_blocked(self):
        steps = iter(
            [
                PlannerAction("čti", tool_name="get_order", tool_args={"order_id": "100245"}),
                PlannerAction("cizí ticket", tool_name="create_ticket", tool_args={"description": "Incident pro 100246"}),
            ]
        )

        def cross_order(_state):
            try:
                return next(steps)
            except StopIteration:
                return PlannerAction("konec", finish=True)

        def boom(*_args):
            raise AssertionError("HITL se pro cizí číslo v popisu ticketu volat nemá")

        runner = agent(approval_callback=boom)
        report = runner.run("Prověř 100245 a vyřeš problém", planner=cross_order)
        self.assertEqual(report.audit_trail[-1].error, "ORDER_SCOPE")

    def test_ops_state_thread_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            ops = OpsState(directory)
            errors = []

            def worker(idx):
                try:
                    for i in range(10):
                        aid = ops.open_approval(f"prompt {idx}-{i}", "execute", "retry_sync", {"order_id": "100245"}, "důvod")
                        ops.remember(f"key-{idx}-{i}", {"status": "OK", "idx": idx, "i": i})
                        rec = ops.recall(f"key-{idx}-{i}")
                        if rec is None or rec.get("i") != i:
                            errors.append(f"recall mismatch {idx}-{i}")
                        ops.settle(aid, {"status": "RESOLVED"})
                        ops.append_run({"report": {"final_status": "RESOLVED", "request": "100245", "audit_trail": []}}, aid)
                except Exception as exc:
                    errors.append(str(exc))

            threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(len(ops.runs()), 50)

    def test_payment_and_sync_errors_handled_cleanly(self):
        class PaymentDown(MockSystems):
            def get_payment(self, order_id):
                return {"status": "ERROR", "code": 500, "message": "Payment gateway timeout"}

        report = agent().run("Prověř 100245", environment=PaymentDown())
        self.assertEqual(report.final_status, ExecutionStatus.FAILED)
        self.assertIn("platby", report.root_cause_summary)

        class SyncDown(MockSystems):
            def get_sync_logs(self, order_id):
                return {"status": "ERROR", "code": 500, "message": "n8n cluster unavailable"}

        report2 = agent().run("Prověř 100245", environment=SyncDown())
        self.assertEqual(report2.final_status, ExecutionStatus.FAILED)
        self.assertIn("synchronizace", report2.root_cause_summary)

    def test_intent_non_imperative_classification(self):
        self.assertEqual(classify_intent("Byla provedena oprava objednávky 100245?"), Intent.PROPOSE)
        self.assertEqual(classify_intent("Je možná oprava synchronizace?"), Intent.PROPOSE)
        self.assertEqual(classify_intent("Byl problém s objednávkou vyřešen?"), Intent.PROPOSE)
        self.assertEqual(classify_intent("Oprav synchronizaci objednávky 100245."), Intent.EXECUTE)

    def test_customer_care_leak_protection(self):
        from agent import _care, _SAFE_CARE
        self.assertEqual(_care("Chyba v Shopify API."), _SAFE_CARE)
        self.assertEqual(_care("Nastala chyba 500 v databázi."), _SAFE_CARE)
        self.assertEqual(_care("Dobrý den, zboží je připraveno k odeslání."), "Dobrý den, zboží je připraveno k odeslání.")


if __name__ == "__main__":
    unittest.main()
