-- 0011_financial_integrity.sql
-- Financial history is append-only. Settled invoices, ledger entries, recorded
-- webhook deliveries and the audit trail can be inserted and read, never edited
-- or deleted. Admins may add internal notes (admin_notes) instead.

CREATE TRIGGER invoices_block_update BEFORE UPDATE ON invoices BEGIN
  SELECT RAISE(ABORT, 'settled invoices are append-only');
END;

CREATE TRIGGER invoices_block_delete BEFORE DELETE ON invoices BEGIN
  SELECT RAISE(ABORT, 'settled invoices cannot be deleted');
END;

CREATE TRIGGER ledger_entries_block_update BEFORE UPDATE ON ledger_entries BEGIN
  SELECT RAISE(ABORT, 'ledger entries are append-only');
END;

CREATE TRIGGER ledger_entries_block_delete BEFORE DELETE ON ledger_entries BEGIN
  SELECT RAISE(ABORT, 'ledger entries cannot be deleted');
END;

CREATE TRIGGER webhook_events_block_update BEFORE UPDATE ON webhook_events BEGIN
  SELECT RAISE(ABORT, 'recorded webhook deliveries are append-only');
END;

CREATE TRIGGER webhook_events_block_delete BEFORE DELETE ON webhook_events BEGIN
  SELECT RAISE(ABORT, 'recorded webhook deliveries cannot be deleted');
END;

CREATE TRIGGER audit_log_block_update BEFORE UPDATE ON audit_log BEGIN
  SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER audit_log_block_delete BEFORE DELETE ON audit_log BEGIN
  SELECT RAISE(ABORT, 'audit log entries cannot be deleted');
END;

-- A settled payment intent keeps its identity: order reference, amount, period
-- and settle timestamp can never be rewritten once settled.
CREATE TRIGGER payment_intents_freeze_settled BEFORE UPDATE ON payment_intents
WHEN OLD.status = 'settled' AND (
     NEW.status             <> OLD.status
  OR NEW.amount_cents       <> OLD.amount_cents
  OR NEW.order_ref          <> OLD.order_ref
  OR NEW.currency           <> OLD.currency
  OR NEW.period_days        <> OLD.period_days
  OR NEW.btcpay_invoice_id  IS NOT OLD.btcpay_invoice_id
  OR NEW.settled_at         IS NOT OLD.settled_at
  OR NEW.tier_id            <> OLD.tier_id
  OR NEW.user_id            <> OLD.user_id
) BEGIN
  SELECT RAISE(ABORT, 'settled payment intent is immutable');
END;

-- Memberships that unlocked access through a settled invoice keep that link.
CREATE TRIGGER memberships_keep_invoice BEFORE UPDATE ON memberships
WHEN OLD.invoice_id IS NOT NULL AND NEW.invoice_id IS NOT OLD.invoice_id BEGIN
  SELECT RAISE(ABORT, 'membership invoice link is immutable');
END;

-- Money must stay balanced inside one invoice row.
CREATE TRIGGER invoices_balance_check BEFORE INSERT ON invoices
WHEN NEW.amount_cents <> NEW.platform_fee_cents + NEW.creator_net_cents BEGIN
  SELECT RAISE(ABORT, 'invoice amounts must balance');
END;

-- Ledger money movements must reference a settled invoice.
CREATE TRIGGER ledger_requires_invoice BEFORE INSERT ON ledger_entries
WHEN NEW.entry_type IN ('member_payment','platform_fee','creator_earning') AND NEW.invoice_id IS NULL BEGIN
  SELECT RAISE(ABORT, 'money movements require a settled invoice');
END;
