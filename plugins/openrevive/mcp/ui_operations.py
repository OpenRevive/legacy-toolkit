"""Bounded, controller-sequenced UI operations over the existing native guards."""
from __future__ import annotations
import json
import math
import time
from ui_observation import matches, postcondition, fingerprint, context


class OperationError(RuntimeError):
    def __init__(self, code, message): self.code, self.message = code, message; super().__init__(message)


def scoped(nodes, selector, tree):
    if not isinstance(selector, dict) or not selector: raise OperationError('INVALID_SELECTOR', 'Supply a nonempty selector')
    if len(json.dumps(selector)) > 6000: raise OperationError('INVALID_SELECTOR', 'Selector too large')
    index = {n['id']: n for n in nodes}
    def match(node, query, depth=0):
        if depth > 3: raise OperationError('INVALID_SELECTOR', 'At most three ancestor scopes')
        allowed = {'label','identifier','role','owner','className','within'}
        if set(query) - allowed: raise OperationError('INVALID_SELECTOR', 'Only exact scoped selectors are accepted for mutation')
        own = {k:v for k,v in query.items() if k != 'within'}
        if not matches(node, own, tree): return False
        if 'within' not in query: return True
        parent, seen = node.get('parent'), set()
        while parent in index and parent not in seen:
            seen.add(parent)
            if match(index[parent], query['within'], depth+1): return True
            parent = index[parent].get('parent')
        return False
    return [n for n in nodes if n.get('visible', True) and match(n, selector)]


class Operations:
    def __init__(self, worker, args):
        self.worker = worker
        self.location = {'host':args.get('host','blacklion'),'target':'iphone','udid':args['udid']}
        self.bundle = args.get('bundle')
        timeout = args.get('timeout', 15)
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not .1 <= timeout <= 120:
            raise OperationError('INVALID_BUDGET', 'Total timeout must be .1..120 seconds')
        self.started = time.monotonic(); self.deadline = self.started + timeout
        self.dispatches, self.steps, self.receipts = 0, 0, []
        self.maximum = args.get('maxDispatches', 12)
        if type(self.maximum) is not int or not 1 <= self.maximum <= 30: raise OperationError('INVALID_BUDGET', 'maxDispatches must be 1..30')
        self.last = None
        self.worker.budget_checker = self.check_budget

    def check_budget(self):
        if time.monotonic()>=self.deadline:
            raise OperationError('BUDGET_EXCEEDED','Total operation budget exhausted; no further primitive will start')

    def checkpoint(self, phase='observing'):
        self.worker.checkpoint(phase)
        if time.monotonic() >= self.deadline: raise OperationError('BUDGET_EXCEEDED', 'Total operation budget exhausted; no further primitive will start')

    def snapshot(self, args=None):
        self.checkpoint()
        args = args or {}
        self.last = self.worker.observe({**self.location,'depth':16,'limit':1000,
            'nodeLimit':args.get('nodeLimit',40),'byteLimit':args.get('byteLimit',12288),'resync':True})
        tree = self.worker.get(self.last['snapshotId'])['tree']
        current = context(tree)
        if self.bundle and current.get('frontmostBundleId',tree.get('bundleId')) != self.bundle:
            raise OperationError('CONTEXT_CHANGED', 'Foreground differs from the explicitly requested bundle')
        if current.get('screenLocked'):
            raise OperationError('DEVICE_LOCKED', 'Unlock before UI operations')
        return tree

    def complete(self, tree):
        return not tree.get('truncated') and tree.get('coverage',{}).get('completeForKnownScreenOwners',True) and not tree.get('coverage',{}).get('truncatedOwners')

    def unique(self, tree, selector):
        if not self.complete(tree): raise OperationError('INCOMPLETE_OBSERVATION', 'Unique target cannot be established from incomplete screen coverage')
        found = scoped(tree['nodes'], selector, tree)
        if not found: raise OperationError('TARGET_NOT_FOUND', 'No visible target matched the exact selector')
        if len(found) != 1: raise OperationError('AMBIGUOUS_TARGET', '%d targets matched; refine the selector' % len(found))
        node = found[0]
        if node.get('enabled') is False or not node.get('actionable'):
            raise OperationError('TARGET_UNAVAILABLE', 'The selected node is disabled or has no guarded action route')
        return node

    def predicate(self, tree, expect, before=None):
        if not isinstance(expect,dict) or expect.get('state') not in ('present','absent','value','foreground','changed'):
            raise OperationError('INVALID_PREDICATE', 'An explicit supported postcondition is required')
        if expect['state'] in ('present','absent','value'):
            found = scoped(tree['nodes'],expect.get('selector'),tree)
            if expect['state'] == 'present': return bool(found)
            if expect['state'] == 'absent': return not found and self.complete(tree)
            if len(found) != 1 or found[0].get('secure'): return False
            attribute = expect.get('attribute', 'isOn' if type(expect.get('value')) is bool and 'isOn' in found[0] else 'value')
            if attribute not in ('value','isOn','enabled','visible','isFirstResponder','contentOffsetPoints'):
                raise OperationError('INVALID_PREDICATE', 'Unsupported value attribute')
            return found[0].get(attribute) == expect.get('value')
        return postcondition(tree, expect, before)

    def verify(self, args, baseline, before, transition=True):
        expect = args['expect']
        seen_false = not baseline
        stable_since, polls = None, 0
        stable_ms = args.get('stableMs',100)
        if type(stable_ms) is not int or not 0 <= stable_ms <= 2000: raise OperationError('INVALID_BUDGET','stableMs must be 0..2000')
        while True:
            self.checkpoint('verifying')
            tree = self.snapshot(args); polls += 1
            matched = self.predicate(tree, expect, before)
            if not matched: seen_false = True; stable_since = None
            if matched and (not transition or seen_false):
                if stable_since is None: stable_since = time.monotonic()
                if (time.monotonic()-stable_since)*1000 >= stable_ms:
                    return {'matched':True,'polls':polls,'transitionObserved':seen_false,
                        'stableMs':round((time.monotonic()-stable_since)*1000)}
            remaining = self.deadline-time.monotonic()
            if remaining <= 0: raise OperationError('VERIFICATION_TIMEOUT','Postcondition was not established within the total budget')
            time.sleep(min(.2,remaining))

    def result(self, code, **extra):
        return {'code':code,'effect':self.worker.execution_effect,'observation':self.last,
            'dispatches':self.dispatches,'elapsedMs':round((time.monotonic()-self.started)*1000),
            'executionScope':'controller_sequenced','automaticRetry':False,
            'verificationScope':'declared UI predicate; server persistence requires an independent receipt',**extra}

    def one(self, args):
        if not self.bundle: raise OperationError('GUARD_UNAVAILABLE','An explicit foreground bundle is required')
        if 'expect' not in args: raise OperationError('INVALID_PREDICATE','perform requires expect')
        tree = self.snapshot(args)
        baseline = self.predicate(tree,args['expect'])
        if args.get('mode','perform') == 'ensure' and baseline:
            return self.result('ALREADY_SATISFIED',verification={'matched':True,'skippedDispatch':True})
        node = self.unique(tree,args.get('selector'))
        global_context = context(tree)
        if global_context.get('surface') not in (None,'application','workspace'):
            raise OperationError('UNEXPECTED_OVERLAY','Use a fresh system context to handle the current overlay')
        if self.dispatches >= self.maximum: raise OperationError('INPUT_BUDGET_EXCEEDED','No more input primitives are allowed')
        action = args.get('action','auto')
        value = args.get('value')
        if action == 'set-text' and (not isinstance(value,str) or len(value) > 4096):
            raise OperationError('INVALID_INPUT','set-text needs a string of at most 4096 characters')
        if action == 'set-on' and type(value) is not bool: raise OperationError('INVALID_INPUT','set-on requires boolean value')
        before = fingerprint(tree)
        self.checkpoint('prepared')
        request = {'snapshotId':self.last['snapshotId'],'nodeId':node['id'],'action':action}
        if 'value' in args: request['value'] = value
        receipt = self.worker._dispatch('act_ui',request)
        self.dispatches += 1; self.receipts.append(receipt)
        verification = self.verify(args,baseline,before,args.get('transition','becomes_true') == 'becomes_true')
        return self.result('VERIFIED',action=receipt,verification=verification)

    def failure(self, error):
        if error.__class__.__name__ == 'Cancelled':
            return self.result('CANCELLED',executorQuiescent=True,
                cancellationScope='no further controller primitives; queued application effects may continue')
        code = getattr(error,'code',None)
        if not isinstance(code,str): code = 'FAILED' if self.worker.execution_effect == 'none' else 'OUTCOME_UNKNOWN'
        if self.dispatches and code not in ('VERIFICATION_TIMEOUT','BUDGET_EXCEEDED'): effect = 'partial'
        else: effect = self.worker.execution_effect
        return self.result(code,error=str(error)[:1800],effect=effect)

    def perform(self, args):
        try: return self.one(args)
        except Exception as error: return self.failure(error)

    def run_steps(self, steps, completed, depth=0):
        if depth > 2 or not isinstance(steps,list) or not 1 <= len(steps) <= 20:
            raise OperationError('INVALID_WORKFLOW','At most 20 steps and two nested branch levels')
        for step in steps:
            self.steps += 1
            if self.steps > 60: raise OperationError('WORKFLOW_BUDGET_EXCEEDED','Workflow expanded beyond 60 steps')
            self.checkpoint('workflow_step')
            kind = step.get('kind')
            if kind == 'perform':
                result = self.one(step)
            elif kind == 'observe':
                self.snapshot(step); result = self.result('OBSERVED')
            elif kind == 'assert':
                if not self.predicate(self.snapshot(step),step['expect']):
                    raise OperationError('ASSERTION_FAILED','Workflow assertion did not hold')
                result = self.result('VERIFIED')
            elif kind == 'wait':
                tree = self.snapshot(step)
                result = self.result('VERIFIED',verification=self.verify(step,
                    self.predicate(tree,step['expect']),fingerprint(tree),step.get('transition','any')=='becomes_true'))
            elif kind == 'ensure_context':
                bundle = step['bundle']
                if self.dispatches >= self.maximum: raise OperationError('INPUT_BUDGET_EXCEEDED','Context activation exceeds input budget')
                receipt = self.worker._dispatch('iphone_input',{**self.location,'operation':'launch','bundle':bundle})
                self.dispatches += 1; self.receipts.append(receipt); self.bundle = bundle
                reads=0
                while True:
                    try:self.snapshot(step);break
                    except Exception as error:
                        # Launch is already acknowledged. iOS can change the
                        # screen owner during its animation; reacquire only the
                        # observation, never repeat the launch or bypass guards.
                        if getattr(error,'code',None)!='CONTEXT_CHANGED'and 'Device bridge screen-changed:'not in str(error):raise
                        reads+=1;self.checkpoint('awaiting_context')
                        time.sleep(min(.1,max(0,self.deadline-time.monotonic())))
                result = self.result('VERIFIED',action=receipt,transitionReadRetries=reads)
            elif kind == 'scroll_find':
                result = self.scroll_find(step)
            elif kind == 'branch':
                selected = step.get('then',[]) if self.predicate(self.snapshot(step),step['expect']) else step.get('else',[])
                result = {'kind':'branch','steps':[]}
                if selected: self.run_steps(selected,result['steps'],depth+1)
            elif kind == 'repeat':
                count = step.get('count',1)
                if type(count) is not int or not 1 <= count <= 5: raise OperationError('INVALID_WORKFLOW','repeat count must be 1..5')
                result = {'kind':'repeat','steps':[]}
                for _ in range(count): self.run_steps(step['steps'],result['steps'],depth+1)
            else: raise OperationError('INVALID_WORKFLOW','Unknown workflow step')
            compact = {k:v for k,v in result.items() if k not in ('observation','verificationScope','automaticRetry','action')}
            if result.get('action'): compact['actionRoute'] = result['action'].get('route')
            self.worker.checkpoint('workflow_result',{'kind':kind,'code':result.get('code'),'step':self.steps})
            completed.append({'kind':kind,'result':compact})

    def scroll_find(self, step):
        maximum = step.get('maxScrolls',4)
        if type(maximum) is not int or not 0 <= maximum <= 10: raise OperationError('INVALID_WORKFLOW','maxScrolls must be 0..10')
        for index in range(maximum+1):
            tree = self.snapshot(step)
            candidates = scoped(tree['nodes'],step['selector'],tree)
            if len(candidates) == 1:
                return self.result('VERIFIED',selected=candidates[0],scrolls=index)
            if len(candidates) > 1: raise OperationError('AMBIGUOUS_TARGET','Refine scroll-find target')
            if index == maximum: break
            container = self.unique(tree,step['container'])
            offset, ranges = container.get('contentOffsetPoints'), container.get('scrollRangePoints')
            if not offset or not ranges: raise OperationError('GUARD_UNAVAILABLE','Container does not expose native scroll bounds')
            delta = step.get('delta',180)
            if type(delta) not in (int,float) or not -1000 <= delta <= 1000 or delta == 0:
                raise OperationError('INVALID_INPUT','Scroll delta must be nonzero within -1000..1000')
            target = [offset[0],max(ranges[1],min(ranges[3],offset[1]+delta))]
            if target == offset: break
            if self.dispatches >= self.maximum: raise OperationError('INPUT_BUDGET_EXCEEDED','Scroll search exhausted input budget')
            self.checkpoint('prepared')
            receipt = self.worker._dispatch('act_ui',{'snapshotId':self.last['snapshotId'],
                'nodeId':container['id'],'action':'scroll','value':target})
            self.dispatches += 1; self.receipts.append(receipt)
        raise OperationError('TARGET_NOT_FOUND','Bounded scroll search stopped; the final viewport is retained')

    def workflow(self, args):
        completed = []
        try:
            self.run_steps(args['steps'],completed)
            return self.result('VERIFIED',completedSteps=completed)
        except Exception as error:
            return {**self.failure(error),'completedSteps':completed,'failedStep':self.steps}

    def contexts(self, args):
        tree = self.snapshot(args)
        native = context(tree)
        contexts = [{'kind':'native','bundle':native.get('frontmostBundleId'),
            'processId':native.get('frontmostPid'),'owners':[{'bundle':o.get('bundleId'),
                'pid':o.get('pid'),'processEpoch':o.get('session')} for o in tree.get('owners',[])],
            'guardScope':'existing unified native snapshot','observation':self.last['revision']}]
        if args.get('includeWeb',True):
            try:
                web = self.worker._dispatch('web_inspect',{**self.location,'operation':'pages','duration':1})
                for page in web.get('pages',[]):
                    if time.monotonic()>=self.deadline:
                        contexts.append({'kind':'web','status':'budget_exhausted'});break
                    try:
                        bound=self.worker._dispatch('web_inspect',{**self.location,'operation':'context','pageId':page['pageId']})
                        contexts.append({'kind':'web',**page,**bound})
                    except Exception as error: contexts.append({'kind':'web',**page,'guardStatus':'unavailable','reason':str(error)[:500]})
            except Exception as error:contexts.append({'kind':'web','status':'unavailable','reason':str(error)[:500]})
        return {'contexts':contexts,'logicalSelectionActivatesPage':False,'observation':self.last}
