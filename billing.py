import stripe
import logging
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
        handle_checkout_completed(session)

    return {"status": "success"}

def handle_checkout_completed(session):
    user_id = session.get('client_reference_id')
    if not user_id:
        logger.error("No user_id in session")
        return

    amount_total = session.get('amount_total') # cents
    unit_amount = Settings.PRICE_PER_CREDIT if hasattr(Settings, 'PRICE_PER_CREDIT') else 100
    credits = amount_total // unit_amount

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(user_id)).first()
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
            reference_id=session.get('id')
        )
        db.add(txn)
        db.commit()
        logger.info(f"Added {credits} credits to user {user_id}")
    except Exception as e:
        logger.error(f"DB Error processing webhook: {e}")
        db.rollback()
    finally:
        db.close()

def reserve_credits(user_id, cost, job_id):
    """
    Deducts credits from user. Raises ValueError if insufficient funds.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
             raise ValueError("User not found")

        if user.balance < cost:
            raise ValueError("Insufficient funds")

        user.balance -= cost
        txn = Transaction(
            user_id=user_id,
            amount=-cost,
            type='reserve',
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

        user = db.query(User).filter(User.id == job.user_id).first()
        if not user: return

        user.balance += cost
        txn = Transaction(
            user_id=user.id,
            amount=cost,
            type='refund',
            reference_id=job_id
        )
        db.add(txn)
        db.commit()
        logger.info(f"Refunded {cost} credits to user {user.id} for job {job_id}")
    except Exception as e:
        logger.error(f"Refund failed: {e}")
        db.rollback()
    finally:
        db.close()
