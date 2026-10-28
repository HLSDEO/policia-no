const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('web/js/utils/navigation_orbit.js', 'utf8');
const block = source.slice(source.indexOf('  var detailParams ='), source.indexOf('  var $scroll ='));
function setup(page, query) {
  const elements = [];
  function $(html) {
    const el = {html, attrs:{}, children:[], value:'', hide(){return this;}, append(v){this.children.push(v);return this;},
      appendTo(parent){parent.children.push(this);return this;}, empty(){this.children=[];return this;},
      attr(k,v){this.attrs[k]=v;return this;}, val(v){if(v===undefined)return this.value;this.value=v;return this;},
      text(v){this.title=v;return this;}, on(event,fn){this.handler=fn;return this;}};
    elements.push(el); return el;
  }
  const context = {location:{pathname:'/'+page,search:query},URLSearchParams,$,icon:()=>'',apply:()=>{}};
  vm.createContext(context); vm.runInContext(block,context);
  context.renderInquiry([{id_inquerito:1,nu_inquerito:'2026.0001'},{id_inquerito:2,nu_inquerito:'2026.0002'}]);
  return context;
}
for(const page of ['inquerito_orbit.html','investigacao.html']) {
  const c=setup(page,'?inquerito=1&doc=99&extracao=80');
  assert.equal(c.hasActiveInquiry,true);
  assert.equal(c.$inquirySelect.value,'1');
  assert.equal(c.$inquirySelect.children.length,2);
  c.$inquirySelect.handler.call({value:'2'});
  assert.equal(c.location.href,page+'?inquerito=2'+(page==='investigacao.html'?'&tema_rota=ignorar':''));
}
for(const page of ['index_orbit.html','inqueritos_orbit.html','mesa_inquerito.html','whatsapp.html']) {
  assert.equal(setup(page,'?inquerito=1').hasActiveInquiry,false);
}
assert.equal(setup('inquerito_orbit.html','').hasActiveInquiry,false);
assert.equal(setup('investigacao.html','?caso=2&inquerito=1').hasActiveInquiry,false);
assert.equal(setup('inquerito_orbit.html','?inquerito=999').hasActiveInquiry,false);
console.log('PASS: detail-only inquiry selector, available items, active selection, clean switch URLs and case exclusion.');
