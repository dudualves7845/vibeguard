"""Execute the frontend renderers to check data stays data, including quotes."""

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class Attributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@unittest.skipUnless(shutil.which("node"), "Node is needed to exercise the frontend")
class TestFrontendData(unittest.TestCase):
    def render(self):
        script = r"""
const fs = require('fs'), vm = require('vm');
let events = {}, children = [], copied = [], requests = [], replacedUrl = '';
let elements = {'search-findings':{value:''}, 'findings-list':{innerHTML:''},
 'toast-container':{appendChild:(node)=>children.push(node)}};
let sandbox = {URLSearchParams, fetch:async(url,options)=>{requests.push({url,headers:options.headers});return {ok:true,json:async()=>({success:true})};},
 window:{location:{hash:'#token=test-session-token',pathname:'/',search:''},history:{replaceState:(_state,_title,url)=>{replacedUrl=url}},
 sessionStorage:{getItem:()=>'',setItem:()=>{}}}, navigator:{clipboard:{writeText:(value)=>{copied.push(value);return Promise.resolve();}}},
 document:{getElementById:(id)=>elements[id],addEventListener:(name,fn)=>{events[name]=fn},
 createElement:()=>({children:[],appendChild(node){this.children.push(node);},textContent:''})},
 setTimeout:()=>{}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('vibeguard/ui_assets/api.js','utf8'),sandbox);
sandbox.escapeHtml=sandbox.window.escapeHtml;
vm.runInContext(fs.readFileSync('vibeguard/ui_assets/app.js','utf8'),sandbox);
sandbox.window.vgState.findings=[{severity:'error',file_path:"x');window.auditSentinel=1;//.py",
 title:'missing',category:'api_breakage',message:'missing',remediation:"Re-implement 'hello' or verify deprecation."}];
sandbox.window.renderFindings();
sandbox.window.showToast('<img src=x onerror=window.auditSentinel=2>');
events.click({target:{closest:()=>({dataset:{copyPath:"x');window.auditSentinel=1;//.py"}})}});
events.click({target:{closest:()=>({dataset:{copyRemedy:encodeURIComponent("Re-implement 'hello' or verify deprecation.")}})}});
(async()=>{await sandbox.window.vgApi('/api/project');
console.log(JSON.stringify({html:elements['findings-list'].innerHTML, copied, requests, replacedUrl,
 toastHtml:children[0].innerHTML || '',toastText:children[0].children.map(x=>x.textContent),
hasClickListener:typeof events.click === 'function'}));})();
"""
        process = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1],
                                 capture_output=True, text=True, check=True)
        return json.loads(process.stdout)

    def test_filenames_and_remedies_are_data_attributes_not_javascript(self):
        result = self.render()
        parser = Attributes()
        parser.feed(result["html"])
        attrs = [attributes for _, attributes in parser.elements]
        self.assertFalse(any("onclick" in attributes for attributes in attrs))
        self.assertTrue(any(attributes.get("data-copy-path") == "x');window.auditSentinel=1;//.py" for attributes in attrs))
        self.assertTrue(any("data-copy-remedy" in attributes for attributes in attrs))
        self.assertTrue(result["hasClickListener"])

    def test_toast_messages_are_rendered_as_text(self):
        result = self.render()
        self.assertEqual(result["toastHtml"], "")
        self.assertIn('<img src=x onerror=window.auditSentinel=2>', result["toastText"])

    def test_copy_buttons_preserve_quotes_and_paths(self):
        self.assertEqual(self.render()["copied"], ["x');window.auditSentinel=1;//.py",
            "Re-implement 'hello' or verify deprecation."])

    def test_fragment_token_is_sent_to_api_and_removed_from_url(self):
        result = self.render()
        self.assertEqual(result["requests"][0]["headers"]["X-VibeGuard-Token"], "test-session-token")
        self.assertEqual(result["replacedUrl"], "/")
