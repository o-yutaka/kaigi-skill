import json, os, pathlib, subprocess, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=pathlib.Path(__file__).resolve().parents[1]; CLI=ROOT/'kaigi'

class Handler(BaseHTTPRequestHandler):
    messages=[{'id':1,'sender':'claude','text':'ready','channel':'general','timestamp':time.time()}]
    sessions=[]
    status={'claude':{'available':True,'role':''},'codex':{'available':True,'role':''},'chatgpt':{'available':True,'role':''}}
    templates=[{'id':'planning','name':'Planning','description':'plan','roles':['planner','challenger','synthesiser']},
               {'id':'debate','name':'Debate','description':'debate','roles':['proposer','for','against','moderator']}]
    def authed(self): return self.headers.get('X-Session-Token')=='testtoken'
    def sendj(self,obj,status=200):
        b=json.dumps(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        from urllib.parse import parse_qs,urlparse
        p=urlparse(self.path)
        if p.path=='/': self.send_response(200); self.end_headers(); return
        if not self.authed(): self.sendj({'error':'auth'},403); return
        if p.path=='/api/messages':
            q=parse_qs(p.query); out=list(self.messages)
            if 'since_id' in q:
                sid=int(q['since_id'][0]); out=[m for m in out if m['id']>sid]
            if 'limit' in q: out=out[-int(q['limit'][0]):]
            self.sendj(out); return
        if p.path=='/api/status': self.sendj(self.status); return
        if p.path=='/api/sessions/templates': self.sendj(self.templates); return
        if p.path=='/api/sessions/active': self.sendj(None); return
        self.sendj({'error':'notfound'},404)
    def do_POST(self):
        if not self.authed(): self.sendj({'error':'auth'},403); return
        n=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(n) or b'{}')
        if self.path=='/api/send':
            msg={'id':len(self.messages)+1,'sender':'user','text':payload['text'],'channel':payload['channel'],'timestamp':time.time()}; self.messages.append(msg); self.sendj(msg)
            text=payload['text']
            def add_agent(sender, body): self.messages.append({'id':len(self.messages)+1,'sender':sender,'text':body,'channel':payload['channel'],'timestamp':time.time()})
            if 'KAIGI COUNCIL / ROUND 1' in text: threading.Timer(0.05, lambda: [add_agent(a, f'{a} independent') for a in ('claude','codex','chatgpt')]).start()
            elif 'KAIGI COUNCIL / ROUND 2' in text: threading.Timer(0.05, lambda: [add_agent(a, f'{a} dissent') for a in ('claude','codex','chatgpt')]).start()
            elif 'KAIGI COUNCIL / FINAL' in text:
                target=text.split()[0].lstrip('@'); threading.Timer(0.05, lambda: add_agent(target, 'DECISION: ship')).start()
            return
        if self.path=='/api/sessions/start':
            tmpl=next(x for x in self.templates if x['id']==payload['template_id']); cast=payload.get('cast') or {r:list(self.status)[i%len(self.status)] for i,r in enumerate(tmpl['roles'])}; s={'id':len(self.sessions)+1,'cast':cast,'template_id':tmpl['id'],'goal':payload['goal']}; self.sessions.append(s); self.sendj(s); return
        self.sendj({'error':'notfound'},404)
    def log_message(self,*_): pass

class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); cls.home=pathlib.Path(cls.tmp.name)/'agentchattr'; cls.home.mkdir()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler); cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.env=os.environ.copy(); cls.env.update({'AGENTCHATTR_SERVER':f'http://127.0.0.1:{cls.port}','KAIGI_TOKEN':'testtoken','NO_COLOR':'1','AGENTCHATTR_HOME':str(cls.home)})
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()
    def run_cli(self,*args,input_text=None): return subprocess.run([str(CLI),*args],env=self.env,text=True,input=input_text,capture_output=True,timeout=5)
    def test_status_log_agents(self):
        r=self.run_cli('status'); self.assertEqual(r.returncode,0,r.stderr); self.assertIn('claude',r.stdout)
        r=self.run_cli('log','100'); self.assertEqual(r.returncode,0,r.stderr); self.assertIn('claude: ready',r.stdout)
        r=self.run_cli('agents'); self.assertEqual(r.returncode,0,r.stderr); self.assertIn('chatgpt',r.stdout)
    def test_say_shortcut(self):
        self.assertEqual(self.run_cli('say','hello','world').returncode,0)
        self.assertEqual(self.run_cli('@claude','check','this').returncode,0); self.assertEqual(Handler.messages[-1]['text'],'@claude check this')
    def test_convene_native_and_cast(self):
        r=self.run_cli('convene','ship','the','feature','--template','planning','--agents','claude,codex,chatgpt','--no-follow'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('会議開始',r.stdout); s=Handler.sessions[-1]; self.assertEqual(s['cast']['planner'],'claude'); self.assertEqual(s['cast']['challenger'],'codex'); self.assertEqual(s['cast']['synthesiser'],'chatgpt')
    def test_convene_council_full(self):
        r=self.run_cli('convene','parallel','decision','--agents','claude,codex,chatgpt','--round-timeout','2')
        self.assertEqual(r.returncode,0,r.stderr); self.assertIn('council完了',r.stdout)
        texts=[m['text'] for m in Handler.messages if m['sender']=='user']
        self.assertTrue(any('ROUND 1' in x for x in texts)); self.assertTrue(any('ROUND 2' in x for x in texts)); self.assertTrue(any('FINAL' in x for x in texts))
    def test_convene_fallback(self):
        r=self.run_cli('convene','fallback','topic','--template','missing','--agents','claude,codex','--no-follow'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('@mention会議',r.stdout); self.assertIn('@claude @codex 会議招集',Handler.messages[-1]['text'])
    def test_chatgpt_setup_idempotent(self):
        r=self.run_cli('chatgpt','setup','--model','gpt-5.6'); self.assertEqual(r.returncode,0,r.stderr)
        r=self.run_cli('chatgpt','setup','--model','gpt-5.6-luna'); self.assertEqual(r.returncode,0,r.stderr)
        text=(self.home/'config.local.toml').read_text(); self.assertEqual(text.count('[agents.chatgpt]'),1); self.assertIn('gpt-5.6-luna',text); self.assertIn('OPENAI_API_KEY',text)
    def test_room_exit(self):
        r=self.run_cli('room','--tail','2',input_text='/quit\n'); self.assertEqual(r.returncode,0,r.stderr); self.assertIn('you ›',r.stdout)

if __name__=='__main__': unittest.main()
