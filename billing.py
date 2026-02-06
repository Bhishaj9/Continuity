import stripe
import logging
from datetime import datetime, timedelta
from fastapi import HTTPException
from config import Settings
from models import SessionLocal, User, Job, Transaction

logger = logging.getLogger(__name__)

if Settings.STRIPE_SECRET_KEY:
    stripe.api_key = Settings.STRIPE_SECRET_KEY

def create_checkout_session(user_id, quantity, success_url, cancel_url):
    if not Settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    try:
        # Calculate amount. Assuming 1 credit = PRICE_PER_CREDIT cents.
        unit_amount = Settings.PRICE_PER_CREDIT if hasattr(Settings, 'PRICE_PER_CREDIT') else 100

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Continuity Credits',
                    },
                    'unit_amount': unit_amount,
                },
                'quantity': quantity,
            }],
            mode='payment',
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(user_id),
            metadata={'user_id': str(user_id)}
        )
        return session.url
    except Exception as e:
        logger.error(f"Stripe Session Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def process_webhook(payload, sig_header):
    if not Settings.STRIPE_WEBHOOK_SECRET:
         raise HTTPException(status_code=500, detail="Webhook secret missing")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, Settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_completed(session, event['id'])

    return {"status": "success"}

def handle_checkout_completed(session, event_id=None):
    user_id = session.get('client_reference_id')
    if not user_id:
        logger.error("No user_id in session")
        return

    amount_total = session.get('amount_total') # cents
    unit_amount = Settings.PRICE_PER_CREDIT if hasattr(Settings, 'PRICE_PER_CREDIT') else 100
    credits = amount_total // unit_amount

    db = SessionLocal()
    try:
        # Idempotency check
        if event_id:
            existing = db.query(Transaction).filter(Transaction.stripe_event_id == event_id).first()
            if existing:
                logger.info(f"Event {event_id} already processed.")
                return

        user = db.query(User).filter(User.id == int(user_id)).with_for_update().first()
        if not user:
            logger.error(f"User {user_id} not found")
            return

        user.balance += credits
        if session.get('customer'):
            user.stripe_customer_id = session.get('customer')

        txn = Transaction(
            user_id=user.id,
            amount=credits,
            type='purchase',
            status='settled',
            reference_id=session.get('id'),
            stripe_event_id=event_id
        )
        db.add(txn)
        db.commit()
        logger.info(f"Added {credits} credits to user {user_id}")
    except Exception as e:
        logger.error(f"DB Error processing webhook: {e}")
        db.rollback()
    finally:
        db.close()

def reconcile_reservations():
    """
    Finds reserved transactions older than 1 hour and refunds them.
    """
    db = SessionLocal()
    refunded_count = 0
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=1)
        stuck_txns = db.query(Transaction).filter(
            Transaction.status == 'reserved',
            Transaction.created_at < cutoff_time
        ).all()

        for txn in stuck_txns:
            # Check associated job status
            should_refund = False
            if txn.reference_id:
                job = db.query(Job).filter(Job.id == txn.reference_id).first()
                if not job:
                    should_refund = True
                else:
                    is_failed = job.status in ['error', 'failed']
                    is_stale = False
                    if job.updated_at and (datetime.utcnow() - job.updated_at) > timedelta(hours=1):
                        is_stale = True

                    if job.status == 'completed':
                        should_refund = False
                    elif is_failed or is_stale:
                        should_refund = True
                    else:
                        should_refund = False
            else:
                should_refund = True

            if not should_refund:
                continue

            user = db.query(User).filter(User.id == txn.user_id).with_for_update().first()
            if not user:
                continue

            # Refund the amount (amount is negative for reserve, so we subtract it or add abs)
            refund_amount = abs(txn.amount)
            user.balance += refund_amount

            refund_txn = Transaction(
                user_id=user.id,
                amount=refund_amount,
                type='refund',
                status='settled',
                reference_id=txn.reference_id,
                stripe_event_id=f"auto-refund-{txn.id}-{datetime.utcnow().timestamp()}"
            )
            db.add(refund_txn)

            txn.status = 'refunded'
            refunded_count += 1
            logger.info(f"Reconciled/Refunded transaction {txn.id} for user {user.id}")

        db.commit()
        return refunded_count
    except Exception as e:
        logger.error(f"Reconciliation failed: {e}")
        db.rollback()
        raise e
    finally:
        db.close()

def reserve_credits(user_id, cost, job_id):
    """
    Deducts credits from user. Raises ValueError if insufficient funds.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).with_for_update().first()
        if not user:
             raise ValueError("User not found")

        if user.balance < cost:
            raise ValueError("Insufficient funds")

        user.balance -= cost
        txn = Transaction(
            user_id=user_id,
            amount=-cost,
            type='reserve',
            status='reserved',
            reference_id=job_id
        )
        db.add(txn)
        db.commit()
        return True
    except ValueError as e:
        db.rollback()
        raise e
    except Exception as e:
        db.rollback()
        logger.error(f"Reservation failed: {e}")
        raise e
    finally:
        db.close()

def refund_credits_by_job_id(job_id, cost):
    """
    Refunds credits to user associated with job_id.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found for refund")
            return

        user = db.query(User).filter(User.id == job.user_id).with_for_update().first()
        if not user: return

        user.balance += cost
        txn = Transaction(
            user_id=user.id,
            amount=cost,
            type='refund',
            status='settled',
            reference_id=job_id
        )
        db.add(txn)

        # Mark original reservation as refunded
        orig_txn = db.query(Transaction).filter(
            Transaction.reference_id == job_id,
            Transaction.type == 'reserve'
        ).first()
        if orig_txn:
            orig_txn.status = 'refunded'

        db.commit()
        logger.info(f"Refunded {cost} credits to user {user.id} for job {job_id}")
    except Exception as e:
        logger.error(f"Refund failed: {e}")
        db.rollback()
    finally:
        db.close()

def settle_transaction(job_id):
    """
    Marks the reservation transaction for the job as settled.
    """
    db = SessionLocal()
    try:
        txn = db.query(Transaction).filter(
            Transaction.reference_id == job_id,
            Transaction.type == 'reserve'
        ).with_for_update().first()
        if txn:
            txn.status = 'settled'
            db.commit()
    except Exception as e:
        logger.error(f"Failed to settle transaction for job {job_id}: {e}")
        db.rollback()
    finally:
        db.close()
