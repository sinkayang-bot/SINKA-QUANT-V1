"""Owner-only command mailbox; exchange credentials and trading stay off this API."""
import time
import uuid

ACTIONS=frozenset({'start','stop','recover','audit','settle'})
LEASE_MS=15000
COMMAND_MS=30000

class ControlBlocked(ValueError):
    pass

class FirestoreControlStore:
    def __init__(self,client,clock=None):
        self.client=client
        self.clock=clock or (lambda:int(time.time()*1000))
        self.ref=client.collection('sinka_demo_control').document('cloudshell_v1')

    def _transaction(self,operation):
        from google.cloud import firestore
        @firestore.transactional
        def change(transaction):
            snap=self.ref.get(transaction=transaction)
            state=snap.to_dict() if snap.exists else {}
            result=operation(state,transaction)
            transaction.set(self.ref,state)
            return result
        return change(self.client.transaction())

    def status(self):
        snap=self.ref.get();state=snap.to_dict() if snap.exists else {}
        return public_status(state,self.clock())

    def issue(self,action,request_id):
        def operation(state,transaction):
            now=self.clock()
            receipt=self.ref.collection('requests').document(request_id)
            prior=receipt.get(transaction=transaction)
            if prior.exists:
                if prior.to_dict().get('action')!=action:raise ControlBlocked('request_id_conflict')
                return {'request_id':request_id,'accepted':True,'duplicate':True}
            if not runner_fresh(state,now): raise ControlBlocked('runner_offline')
            if state.get('pending'): raise ControlBlocked('command_pending')
            if action!='stop' and state.get('busy'): raise ControlBlocked('runner_busy')
            if action=='stop' and not state.get('busy'): raise ControlBlocked('nothing_running')
            state.update(pending={'action':action,'request_id':request_id,'expires':now+COMMAND_MS,
                'runner':state['runner'],'generation':state['generation'],'target':state.get('active_request')},
                last_request_id=request_id)
            transaction.set(receipt,{'action':action,'created_at':now})
            return {'request_id':request_id,'accepted':True,'duplicate':False}
        if action not in ACTIONS: raise ControlBlocked('unsupported_action')
        try:
            if str(uuid.UUID(request_id))!=request_id: raise ValueError()
        except (ValueError,TypeError,AttributeError): raise ControlBlocked('invalid_request_id') from None
        return self._transaction(operation)

    def acquire(self,runner):
        def operation(state,transaction):
            now=self.clock()
            if state.get('lease_until',0)>now and state.get('runner')!=runner:
                raise ControlBlocked('runner_already_connected')
            generation=state.get('generation',0)+1
            # Do not replay a command whose process may have started before a crash.
            interrupted=bool(state.get('busy'))
            state.update(runner=runner,generation=generation,lease_until=now+LEASE_MS,
                heartbeat=now,busy=False,pending=None,active_request=None,
                phase='interrupted_check_required' if interrupted else 'ready',
                recovery_required=interrupted or bool(state.get('recovery_required')))
            return generation
        return self._transaction(operation)

    def poll(self,runner,generation):
        def operation(state,transaction):
            now=self.clock();owned(state,runner,generation,now)
            state.update(lease_until=now+LEASE_MS,heartbeat=now)
            command=state.get('pending')
            if not command: return None
            state['pending']=None
            if (command['expires']<now or command['runner']!=runner or command['generation']!=generation):
                state['phase']='command_expired';return None
            if command['action']=='stop':
                if not state.get('busy') or command.get('target')!=state.get('active_request'):
                    state['phase']='stop_target_changed';return None
            else:
                if state.get('busy'): raise ControlBlocked('runner_busy')
                if state.get('recovery_required') and command['action']=='start':
                    state['phase']='recovery_required';return None
                state.update(busy=True,active_request=command['request_id'],phase='starting')
            return command
        return self._transaction(operation)

    def publish(self,runner,generation,request_id,report,*,finished=False):
        def operation(state,transaction):
            now=self.clock();owned(state,runner,generation,now)
            if state.get('active_request')!=request_id: raise ControlBlocked('command_owner_changed')
            if report!=state.get('last_result'):
                history=state.get('history',[])
                history.append({'at':now,'request_id':request_id,'result':report})
                state['history']=history[-30:]
            state.update(last_result=report,phase=report.get('phase','result_available'),heartbeat=now,
                lease_until=now+LEASE_MS,updated_at=now)
            if finished:
                state.update(busy=False,active_request=None)
                # A new start still passes the original durable exchange/ledger gates.
                state['recovery_required']=report.get('phase') not in {
                    'stopped_no_active_intent','tracked_strategy_exit_closed_and_archived',
                    'no_active_intent_no_submission','idle_verified','baseline_initialized'}
            return None
        return self._transaction(operation)


def runner_fresh(state,now):
    return (0<=now-state.get('heartbeat',0)<=10000 and state.get('lease_until',0)>now)


def owned(state,runner,generation,now):
    if (state.get('runner')!=runner or state.get('generation')!=generation
            or state.get('lease_until',0)<=now):
        raise ControlBlocked('runner_lease_lost')


def public_status(state,now):
    return dict(mode='okx_demo_control',live_trading_enabled=False,
        runner_online=runner_fresh(state,now),busy=bool(state.get('busy')),
        phase=state.get('phase','runner_not_connected'),command_pending=bool(state.get('pending')),
        recovery_required=bool(state.get('recovery_required')),last_result=state.get('last_result'),
        heartbeat_at=state.get('heartbeat'),updated_at=state.get('updated_at'),
        active_request=state.get('active_request'),history=state.get('history',[]),checked_at=now)
