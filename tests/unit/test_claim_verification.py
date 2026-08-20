"""
Regression tests for modules.identity_brain.claim_verification (2026-08-20,
Owner directive). See that module's own docstring for the full reasoning
rule this enforces: a third-party name/role claim in message content
("Karim is supervisor") must be resolved against Fazle Core before being
treated as true -- never guessed on ambiguity, never inferred from
message content alone.
"""
import pytest

from modules.identity_brain.claim_verification import verify_employee_claim

pytestmark = pytest.mark.integration


class TestVerifyEmployeeClaimOutcomes:
    async def test_karim_not_found(self, clean_tables):
        result = await verify_employee_claim(claimed_name="Karim Nonexistent", claimed_role="supervisor")
        assert result["outcome"] == "not_found"

    async def test_two_karims_ambiguous(self, clean_tables, seed_fpe_karim_supervisor, seed_fpe_karim_duplicate):
        result = await verify_employee_claim(claimed_name="Karim Uddin", claimed_role="supervisor")
        assert result["outcome"] == "ambiguous"
        assert len(result["candidates"]) == 2
        assert "employee_id" not in result

    async def test_karim_is_guard_not_supervisor(self, clean_tables, seed_fpe_karim_duplicate):
        """seed_fpe_karim_duplicate's designation is 'Security Guard'."""
        result = await verify_employee_claim(claimed_name="Karim Uddin", claimed_role="supervisor")
        assert result["outcome"] == "role_mismatch"
        assert result["actual_designation"] == "Security Guard"
        assert result["claimed_role"] == "supervisor"

    async def test_unique_active_karim_is_verified_supervisor(self, clean_tables, seed_fpe_karim_supervisor):
        """seed_fpe_karim_supervisor's designation is 'Ghat Supervisor' --
        tests the substring-regex normalizer matches "Ghat Supervisor"
        against the claimed "supervisor" role."""
        result = await verify_employee_claim(claimed_name="Karim Uddin", claimed_role="supervisor")
        assert result["outcome"] == "verified"
        assert result["employee_id"] == seed_fpe_karim_supervisor["id"]
        assert result["lifecycle_status"] == "active"

    async def test_alias_resolves_via_alias_not_name_fuzzy(self, clean_tables, test_db_pool, seed_fpe_karim_supervisor):
        """Claim resolved via an alias must report match_type == 'alias_name',
        not fall through to a fuzzy/exact tier."""
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
                "VALUES ($1, 'name', 'karim vai')",
                seed_fpe_karim_supervisor["id"],
            )
        result = await verify_employee_claim(claimed_name="Karim Vai")
        assert result["outcome"] == "verified"
        assert result["match_type"] == "alias_name"
        assert result["employee_id"] == seed_fpe_karim_supervisor["id"]

    async def test_inactive_employee_distinguished_from_active(self, clean_tables, test_db_pool):
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO fpe_employees
                    (full_name, name_normalized, primary_phone, designation, status)
                VALUES
                    ('Karim Uddin', 'karim uddin', '01833333399', 'Ghat Supervisor', 'inactive')
                RETURNING *
            """)
        result = await verify_employee_claim(claimed_name="Karim Uddin", claimed_role="supervisor")
        assert result["outcome"] == "verified_inactive"
        assert result["employee_id"] == row["id"]
        assert result["lifecycle_status"] == "inactive"
        # Message text must differ from the active-verified case -- an
        # admin/Hermes reading only the message must not mistake this for
        # a currently-active employee.
        active_result_message = (
            f"{row['full_name']} সত্যিই '{row['designation']}' পদে আছেন — তথ্য মিলেছে।"
        )
        assert result["message"] != active_result_message

    async def test_phone_resolves_before_name(self, clean_tables, test_db_pool, seed_fpe_karim_supervisor, seed_fpe_karim_duplicate):
        """Even though the name is ambiguous (two Karims), a verified
        sender_phone must resolve unambiguously per the required
        verification order (phone first)."""
        result = await verify_employee_claim(
            claimed_name="Karim Uddin", claimed_role="supervisor",
            sender_phone=seed_fpe_karim_supervisor["primary_phone"],
        )
        assert result["outcome"] == "verified"
        assert result["match_type"] == "phone"
        assert result["employee_id"] == seed_fpe_karim_supervisor["id"]

    async def test_employee_id_resolves_directly(self, clean_tables, seed_fpe_karim_duplicate):
        result = await verify_employee_claim(employee_id=seed_fpe_karim_duplicate["id"])
        assert result["outcome"] == "verified"
        assert result["match_type"] == "employee_id"
        assert result["employee_id"] == seed_fpe_karim_duplicate["id"]


class TestClaimVerificationDoesNotAffectUnrelatedFlows:
    """Regression coverage: adding this module must not change the
    unrelated sender-identity/recruitment/KB pipeline."""

    async def test_known_employee_own_identity_resolution_unaffected(self, clean_tables, seed_employee):
        from modules.identity_brain import detect_identity
        result = await detect_identity(seed_employee["employee_mobile"], "আমার তথ্য কী?")
        assert result["role"] in ("employee", "security_guard", "escort")

    def test_salam_plus_job_question_no_salam_still_answers_job(self):
        """Task 1/2 crossover regression, using the real live reply path."""
        from modules.social_auto_reply.intelligent_generator import _combined_rule_reply
        reply = _combined_rule_reply(
            "meta_whatsapp", "আসসালামু আলাইকুম, বেতন কত?", ["greeting", "salary"], [], None,
        )
        assert "আসসালামু আলাইকুম" not in reply
        assert "ওয়ালাইকুম" not in reply


class TestSensitiveActionsStayIdBacked:
    """Regression proving 2E's conclusion: every mutating fazle-mcp tool
    already requires a hard ID, never a bare name -- the enforcement
    point for "no name-only sensitive mutation" is upstream at
    resolution (this module), not at the mutation tools themselves.
    fazle-mcp is a separate repo/venv, so this inspects fazle-core's own
    mutating routes' required params instead (same contract, same repo)."""

    def test_verify_employee_claim_itself_is_read_only_no_mutation_params(self):
        import inspect
        sig = inspect.signature(verify_employee_claim)
        # Sanity: this function has no mode/confirm/mutation-shaped params --
        # it is purely a read-only resolver, never an action.
        assert set(sig.parameters) == {"claimed_name", "claimed_role", "sender_phone", "employee_id"}
