"""Pending host review of bounded template glue APIs; never automatic approval.

This reads source syntax, not TeX expansion. Current compile/all-page binding is
owned by source_review; this module additionally checks actual imported templates.
"""
from __future__ import annotations
import hashlib, math, re
from pathlib import Path
try:
 from scientific_integrity import active_source, scientific_payload_diff, LAYOUT_GLUE_REGISTERS, _macro_definitions
 from layout_prose_integrity import compare_layout_prose
 from source_map import _comment_syntax, _definition_end, group, line_at, masked_tex
except ImportError:
 from .scientific_integrity import active_source, scientific_payload_diff, LAYOUT_GLUE_REGISTERS, _macro_definitions
 from .layout_prose_integrity import compare_layout_prose
 from .source_map import _comment_syntax, _definition_end, group, line_at, masked_tex

_NAME=r'[A-Za-z@]+'
_NUMBER=r'(?:\d+(?:\.\d*)?|\.\d+)'
_UNITS=r'(?:pt|em|ex|cm|mm|in|bp|pc|dd|cc|sp)'
_REGISTERS='|'.join(sorted(LAYOUT_GLUE_REGISTERS))

def _glue(value, *, native=False):
 atomic=_NUMBER+r'\s*'+_UNITS
 if native:atomic=r'(?:'+atomic+r'|(?:'+_NUMBER+r'\s*)?\\(?:'+_REGISTERS+r'))'
 if not re.fullmatch(r'\s*'+atomic+r'(?:\s+plus\s+'+atomic+r')?(?:\s+minus\s+'+atomic+r')?\s*',value):return False
 numbers=[float(x) for x in re.findall(_NUMBER,value)]
 return all(math.isfinite(x) and x>=0 for x in numbers) and (native or bool(numbers) and numbers[0]>0)

def _definitions(source, *, names=None, commands=None):
 syntax=_comment_syntax(active_source(source));records=[]
 for marker in re.finditer(r'\\(renewcommand|newcommand|providecommand|def|gdef)(?![A-Za-z@])',syntax):
  command=marker[1];pos=marker.end()
  if commands is not None and command not in commands:continue
  while pos<len(syntax) and syntax[pos].isspace():pos+=1
  if pos<len(syntax) and syntax[pos]=='*':pos+=1
  target=group(syntax,pos)
  if target:macro,pos=target
  else:
   target=re.match(r'\s*(\\'+_NAME+r')',syntax[pos:])
   if not target:continue
   macro=target[1];pos+=target.end()
  if not re.fullmatch(r'\\'+_NAME,macro.strip()):continue
  if names is not None and macro.strip()[1:] not in names:continue
  optional=group(syntax,pos,'[',']')
  if optional:
   if optional[0].strip()!='0':continue
   pos=optional[1]
  body=group(syntax,pos)
  if not body:continue
  value,end=body
  if _definition_end(syntax,marker.end(),command)!=end:continue
  shielded=syntax[:marker.start()+1]+'Z'*len(command)+syntax[marker.end():]
  if masked_tex(shielded)[marker.start()]!='\\':continue
  records.append({'macro':macro.strip()[1:],'command':command,'value':value.strip(),
                  'start':marker.start(),'end':end,'line':line_at(source,marker.start()),
                  'target_start':syntax.find(macro.strip(),marker.end(),end)})
 return records

def _shadowed(source,names):
 if any(r['macro'] in names for r in _definitions(source,names=names)):return True
 for record in _macro_definitions(active_source(source)):
  target=record[1] if record[0] in {'def','gdef','edef','xdef','let'} else record[2]
  if str(target).lstrip('\\') in names:return True
 return False

def _remove(source,records):
 chars=list(source)
 for record in records:
  for i in range(record['start'],record['end']):
   if chars[i] not in '\r\n':chars[i]=' '
 return ''.join(chars)

def classify_template_glue_override(original:str,repaired:str)->dict:
 result={'checked':True,'status':'not_applicable'}
 if _shadowed(original,{'renewcommand'}) or _shadowed(repaired,{'renewcommand'}):return result
 old=_definitions(original,commands={'renewcommand'});new=_definitions(repaired,commands={'renewcommand'})
 old_by={r['macro']:[x for x in old if x['macro']==r['macro']] for r in old}
 new_by={r['macro']:[x for x in new if x['macro']==r['macro']] for r in new}
 changes=[];removed=[]
 for name,rows in new_by.items():
  prior=old_by.get(name,[])
  signature=lambda rs:[(r['command'],r['value']) for r in rs]
  if signature(prior)==signature(rows):continue
  if len(rows)!=1 or len(prior)>1 or rows[0]['command']!='renewcommand' or not _glue(rows[0]['value']):return result
  if prior and (prior[0]['command']!='renewcommand' or not _glue(prior[0]['value'])):return result
  changes+=rows;removed+=prior
 if not changes:return result
 left=_remove(original,removed);right=_remove(repaired,changes)
 science=scientific_payload_diff(left,right,_template_glue_review=False);prose=compare_layout_prose(left,right)
 families=[]
 if science.get('status')!='pass':
  uncertainty=science.get('layout_argument_uncertainty',{})
  if science.get('assessment',{}).get('classification')!='manual_review' or uncertainty.get('status')!='pending':return result
  families.append('layout_argument_uncertainty')
 if prose.get('status')!='pass':return result
 return {'checked':True,'status':'pending','macros':[r['macro'] for r in changes],
         'overrides':changes,'residual_science':science,'layout_prose':prose,'additional_families':families,
         'requires_host_review':True,'verification_method':'explicit_host_review',
         'scope_note':'Bounded literal override only; actual native declaration and every imported use still require verification.'}

def _loaded(root,files):
 root=Path(root).resolve();loaded={}
 if not isinstance(files,dict) or not files:raise ValueError('actual compiled imported input map required')
 for relative,digest in files.items():
  path=(root/relative).resolve()
  if Path(relative).is_absolute() or not path.is_relative_to(root) or not path.is_file():raise ValueError('imported input outside root/unavailable')
  if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('imported input hash changed')
  if path.suffix.lower() in {'.tex','.bib','.bst','.cls','.sty','.ltx','.def','.cfg','.clo'}:loaded[relative]=path.read_text(encoding='utf-8',errors='surrogateescape')
 if not loaded:raise ValueError('no actual compiled source/template inputs')
 return loaded

def _consumers(source,macro):
 syntax=_comment_syntax(active_source(source));name=re.escape(macro)
 # Every match consumes exactly the macro, never other expression or code.
 patterns=[r'\\(?P<register>'+_REGISTERS+r')(?![A-Za-z@])\s*(?:=\s*)?\\'+name+r'(?![A-Za-z@])',
           r'\\setlength(?![A-Za-z@])\s*\{\s*\\(?P<register>'+_REGISTERS+r')\s*\}\s*\{\s*\\'+name+r'(?![A-Za-z@])\s*\}']
 matches=[]
 for pattern in patterns:
  for match in re.finditer(pattern,syntax):
   matches.append({'start':match.start(),'end':match.end(),'line':line_at(source,match.start()),'register':match['register']})
 return matches

def suggest_evidence(root,imported_inputs_files,classification):
 loaded=_loaded(root,imported_inputs_files);suggestions=[]
 if classification.get('status')!='pending':return suggestions
 for macro in classification['macros']:
  for file,source in loaded.items():
   if Path(file).suffix.lower() not in {'.cls','.sty'}:continue
   declarations=[r for r in _definitions(source,names={macro}) if r['macro']==macro and _glue(r['value'],native=True)]
   for declaration in declarations[:1]:
    for consumer in _consumers(source,macro):
     suggestions.append({'macro':macro,'file':file,'sha256':imported_inputs_files[file],
       'declaration_line':declaration['line'],'consumer_line':consumer['line'],'template_checked':False})
 return suggestions

collect_template_glue_evidence=suggest_evidence

def verify_template_glue_evidence(root,imported_inputs_files,classification,template_parameter_evidence):
 if classification.get('status')!='pending':raise ValueError('no pending template glue override')
 loaded=_loaded(root,imported_inputs_files);proofs=[]
 if not isinstance(template_parameter_evidence,list):raise ValueError('template parameter evidence list required')
 for override in classification['overrides']:
  macro=override['macro'];items=[e for e in template_parameter_evidence if isinstance(e,dict) and e.get('macro')==macro]
  if len(items)!=1:raise ValueError('one exact template parameter evidence per macro required')
  evidence=items[0];file=evidence.get('file');source=loaded.get(file)
  if evidence.get('template_checked') is not True or source is None or Path(file).suffix.lower() not in {'.cls','.sty'}:raise ValueError('imported project template must be explicitly inspected')
  if evidence.get('sha256')!=imported_inputs_files[file]:raise ValueError('template evidence hash mismatch')
  declarations=[r for r in _definitions(source,names={macro}) if r['macro']==macro and _glue(r['value'],native=True)]
  consumers=_consumers(source,macro)
  if not any(r['line']==evidence.get('declaration_line') for r in declarations) or not any(r['line']==evidence.get('consumer_line') for r in consumers):raise ValueError('native zero-argument glue declaration/direct consumer evidence absent')
  saw_override=False
  consumer_registers={u['register'] for text in loaded.values() for u in _consumers(text,macro)}
  for relative,text in loaded.items():
   definitions=_definitions(text,names={macro});uses=_consumers(text,macro)
   if _shadowed(text,{'renewcommand','setlength'}|consumer_registers):raise ValueError('layout consumer command/register is shadowed')
   allowed=[]
   for definition in definitions:
    if definition['macro']!=macro:continue
    if not _glue(definition['value'],native=Path(relative).suffix.lower() in {'.cls','.sty'}):raise ValueError('unknown template glue definition')
    allowed.append((definition['target_start'],definition['target_start']+len(macro)+1))
    if definition['command']=='renewcommand' and definition['value']==override['value']:saw_override=True
   syntax=_comment_syntax(active_source(text))
   for match in re.finditer(r'\\'+re.escape(macro)+r'(?![A-Za-z@])',syntax):
    if any(a<=match.start()<b for a,b in allowed):continue
    if any(u['start']<=match.start()<u['end'] for u in uses):continue
    raise ValueError('macro has indirect, scientific, or unsupported imported use: '+relative)
  if not saw_override:raise ValueError('current imported source lacks the exact override')
  proofs.append(dict(evidence))
 return {'status':'verified','verification_method':'explicit_host_review','template_parameter_evidence':proofs,
         'scope_note':'Native template syntax only; caller must verify current compile/PDF/all pages and host source/template confirmations.'}
