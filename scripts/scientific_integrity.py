"""Conservative LaTeX scientific payload invariants, independent of prose hashes.

This verifies token preservation, not mathematical equivalence. Only explicit
presentation controls are normalized; unsupported rewrites require host review.
"""
from __future__ import annotations
import hashlib,json,re
from collections import Counter
from typing import Any,Dict,List,Tuple

MATH_ENVS={'equation','equation*','align','align*','alignat','alignat*','flalign','flalign*','multline','multline*','gather','gather*','displaymath','math','eqnarray','eqnarray*','IEEEeqnarray','IEEEeqnarray*','IEEEeqnarraybox','IEEEeqnarraybox*','dmath','dmath*','dgroup','dgroup*'}
MATH_WRAPPERS=MATH_ENVS|{'aligned','alignedat','split','multlined','gathered'}
TABLE_ENVS={'tabular','tabular*','tabularx','longtable'}
FLOAT_ENVS={'figure','figure*','table','table*','wrapfigure','wraptable','sidewaysfigure','sidewaysfigure*','sidewaystable','sidewaystable*'}
FONT_DECL={'tiny','scriptsize','footnotesize','small','normalsize','large','Large','LARGE','huge','Huge','bfseries','mdseries','itshape','upshape','slshape','scshape','rmfamily','sffamily','ttfamily','normalfont','centering','raggedright','raggedleft','raggedbottom','flushleft','flushright','FloatBarrier','clearpage','newpage','pagebreak','nopagebreak','noindent','medskip','smallskip','bigskip','hfill','vfill','par','arraybackslash'}
TEXT_WRAPPERS={'textbf','textit','texttt','textrm','textsf','textnormal','emph','underline','mbox','makecell','nolinkurl'}
ID_COMMANDS={'label','ref','eqref','pageref','autoref','cref','Cref','vref','nameref','cite','citep','citet','citealp','citealt','citeauthor','citeyear','citeyearpar','nocite'}
PROTECTED_ARGS={'caption','subcaption','section','subsection','subsubsection','paragraph','title','author','affiliation','thanks','footnote','footnotetext'}
LAYOUT_ONE={'vspace','hspace','vskip','hskip','fontsize','linespread','color','cellcolor','rowcolor','cline','cmidrule','addlinespace','bibliographystyle'}
RULES={'toprule','midrule','bottomrule','hline','hlineB'}
DELIMITER_SIZE_COMMANDS={'left','right'}|{size+suffix for size in ('big','Big','bigg','Bigg') for suffix in ('','l','r','m')}
DELIMITER_COMMANDS={'{','}','|','lbrace','rbrace','langle','rangle','lvert','rvert','lVert','rVert','vert','Vert','lfloor','rfloor','lceil','rceil','backslash'}
LAYOUT_LENGTH_REGISTERS={'tabcolsep','arrayrulewidth','textheight','textwidth','columnsep','columnwidth','linewidth','abovecaptionskip','belowcaptionskip','floatsep','textfloatsep','intextsep','parskip','parindent','baselineskip','emergencystretch','abovedisplayskip','belowdisplayskip','abovedisplayshortskip','belowdisplayshortskip','itemsep','topsep','parsep','partopsep','leftmargin','rightmargin','listparindent','itemindent','labelsep','labelwidth'}
# Only standard glue registers accept stretch/shrink components. Dimension
# registers do not consume trailing prose such as "plus 91.2".
LAYOUT_GLUE_REGISTERS={'abovecaptionskip','belowcaptionskip','floatsep','textfloatsep','intextsep','parskip','baselineskip','abovedisplayskip','belowdisplayskip','abovedisplayshortskip','belowdisplayshortskip','itemsep','topsep','parsep','partopsep'}
TEXT_SPACE_COMMANDS={'quad','qquad'}
REGISTER_DECLARATIONS={'newlength','newdimen','newskip','newmuskip','dimendef','skipdef','muskipdef','countdef','chardef','mathchardef','toksdef'}

def _command(s:str,i:int)->Tuple[str,int]:
 m=re.match(r'\\([A-Za-z@]+|.)',s[i:],re.S)
 if not m:return '',i+1
 return m.group(1),i+len(m.group(0))

def _group(s:str,i:int,opening:str='{',closing:str='}')->Tuple[str,int]:
 while i<len(s) and s[i].isspace():i+=1
 if i>=len(s) or s[i]!=opening:return '',i
 depth=1;j=i+1
 while j<len(s):
  if s[j]=='\\':_,j=_command(s,j);continue
  if s[j]==opening:depth+=1
  elif s[j]==closing:
   depth-=1
   if not depth:return s[i+1:j],j+1
  j+=1
 return '',i

def _optional(s:str,i:int)->int:
 while i<len(s) and s[i].isspace():i+=1
 if i<len(s) and s[i]=='[':_,j=_group(s,i,'[',']');return j
 return i

def _literal_dimension(value:str)->bool:
 # A bounded spelling of a TeX length, not arbitrary macros or expressions.
 number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
 return bool(re.fullmatch(r'\s*(?:'+number+r'\s*(?:pt|em|ex|cm|mm|in|bp|pc|dd|cc|sp)|(?:'+number+r'\s*)?\\(?:linewidth|columnwidth|textwidth|textheight))\s*',value))

def _layout_dimension_pattern(*,expressions:bool=True)->str:
 number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
 registers='|'.join(sorted(LAYOUT_LENGTH_REGISTERS))
 atomic=r'(?:'+number+r'\s*(?:pt|em|ex|cm|mm|in|bp|pc|dd|cc|sp)(?![A-Za-z@])|(?:'+number+r'\s*)?\\(?:'+registers+r')(?![A-Za-z@]))'
 if not expressions:return atomic
 # Literal dimexpr arithmetic only: no arbitrary commands, variables, units,
 # expandable counters or zero division. This is syntax, not length approval.
 expression=r'\\dimexpr(?![A-Za-z@])\s*'+atomic+r'(?:\s*(?:[+-]\s*'+atomic+r'|[*/]\s*[1-9]\d*(?![\d.])))*\s*\\relax(?![A-Za-z@])'
 return r'(?:'+expression+r'|'+atomic+r')'

def _layout_length_pattern(register:str)->str:
 dimension=_layout_dimension_pattern(expressions=register!='emergencystretch')
 if register not in LAYOUT_GLUE_REGISTERS:return dimension
 number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
 stretch=r'(?:'+dimension+r'|'+number+r'\s*fil(?:l{0,2})(?![A-Za-z@]))'
 return dimension+r'(?:\s+plus\s+'+stretch+r')?(?:\s+minus\s+'+stretch+r')?'

def _length_commands_safe(value:str,opaque_commands:frozenset[str])->bool:
 return not any(name in opaque_commands for name in re.findall(r'\\([A-Za-z@]+)',value))

def layout_length_end(s:str,i:int,*,opaque_commands:frozenset[str]=frozenset())->int|None:
 """Parse a known register and finite literal layout expression.

 Known register references and bounded dimexpr arithmetic are presentation;
 dynamic/redefined commands and incomplete expressions remain protected.
 This does not approve font size, compression or template changes.
 """
 pos=i
 while pos<len(s) and s[pos].isspace():pos+=1
 if pos<len(s) and s[pos]=='{':
  register,end=_group(s,pos)
  if end==pos:return None
  register=register.strip()
 elif pos<len(s) and s[pos]=='\\':
  name,end=_command(s,pos);register='\\'+name
 else:return None
 if register not in {'\\'+name for name in LAYOUT_LENGTH_REGISTERS} or register[1:] in opaque_commands:return None
 pos=end
 while pos<len(s) and s[pos].isspace():pos+=1
 if pos>=len(s) or s[pos]!='{':return None
 value,end=_group(s,pos)
 if end==pos:return None
 if not re.fullmatch(r'\s*'+_layout_length_pattern(register[1:])+r'\s*',value):return None
 if not _length_commands_safe(value,opaque_commands):return None
 return end

def layout_assignment_end(s:str,i:int,register:str,*,opaque_commands:frozenset[str]=frozenset())->int|None:
 """Parse a primitive assignment without swallowing following body tokens."""
 if register not in LAYOUT_LENGTH_REGISTERS or register in opaque_commands:return None
 # Preserve the established narrower dimension-only emergency-stretch grammar.
 if register=='emergencystretch':return emergency_stretch_end(s,i,opaque_commands=opaque_commands)
 value=re.match(r'\s*(?:=\s*)?'+_layout_length_pattern(register),s[i:])
 if value is None or not _length_commands_safe(value.group(),opaque_commands):return None
 end=i+value.end()
 # A malformed glue component must not be partially normalized. For dimension
 # registers, "plus/minus ..." remains ordinary protected text instead.
 if register in LAYOUT_GLUE_REGISTERS and re.match(r'\s+(?:plus|minus)(?![A-Za-z@])',s[end:]):return None
 return end

def opaque_length_args_end(s:str,i:int)->int:
 """Keep unsupported length-wrapper arguments intact, never reparse children.

 This bounds a known wrapper's two literal arguments without approving their
 meaning. An incomplete group/expression conservatively preserves the remaining
 source; no text or numbers are discarded from the scientific channel.
 """
 pos=i
 while pos<len(s) and s[pos].isspace():pos+=1
 if pos>=len(s):return pos
 if s[pos]=='{':
  _,end=_group(s,pos)
  if end==pos:return len(s)
 elif s[pos]=='\\':_,end=_command(s,pos)
 else:end=pos+1
 pos=end
 while pos<len(s) and s[pos].isspace():pos+=1
 if pos>=len(s):return end
 if s[pos]=='{':
  _,end=_group(s,pos)
  return end if end>pos else len(s)
 value=re.match(_layout_length_pattern('parskip'),s[pos:])
 if value:return pos+value.end()
 if s[pos]=='\\':
  name,end=_command(s,pos)
  if name=='dimexpr':
   terminator=re.search(r'\\relax(?![A-Za-z@])',s[end:])
   return end+terminator.end() if terminator else len(s)
  while True:
   cursor=end
   while cursor<len(s) and s[cursor].isspace():cursor+=1
   if cursor>=len(s) or s[cursor] not in '{[':break
   opening=s[cursor];_,stop=_group(s,cursor,opening,']' if opening=='[' else '}')
   if stop==cursor:return len(s)
   end=stop
  return end
 return pos+1

def opaque_layout_child_end(s:str,i:int)->int:
 """A reader/unknown macro may consume the next layout control as an argument.

 Preserve only balanced literal groups and the adjacent known layout token with
 its finite assignment/length arguments. Do not guess macro arity or swallow a
 following ordinary word or scientific number outside that spelling.
 """
 pos=i
 while True:
  cursor=pos
  while cursor<len(s) and s[cursor].isspace():cursor+=1
  if cursor>=len(s) or s[cursor] not in '[{':break
  opening=s[cursor];_,end=_group(s,cursor,opening,']' if opening=='[' else '}')
  if end==cursor:return i
  pos=end
 cursor=pos
 while cursor<len(s) and s[cursor].isspace():cursor+=1
 if cursor>=len(s) or s[cursor]!='\\':return i
 name,end=_command(s,cursor)
 if name in LAYOUT_LENGTH_REGISTERS:
  assignment=layout_assignment_end(s,end,name)
  return assignment if assignment is not None else end
 if name in {'setlength','addtolength'}:
  if end<len(s) and s[end]=='*':end+=1
  return opaque_length_args_end(s,end)
 return i

def arraystretch_declarations(source:str)->List[Dict[str,Any]]:
 """Finite no-argument arraystretch declarations at executable source positions.

 Candidate-head substitution is length preserving. The shared source_map mask
 still suppresses comments, literals, stringified controls and unused enclosing
 macro definitions. This inventories syntax, not TeX expansion or visual quality.
 """
 from source_map import _comment_syntax, _definition_end, group, line_at, masked_tex
 syntax=_comment_syntax(source)
 active=active_source(source)  # also masks literal filecontents containers
 records=[]
 for marker in re.finditer(r'\\(newcommand|renewcommand|providecommand|def|gdef)(?![A-Za-z@])',syntax):
  name=marker[1];pos=marker.end()
  while pos<len(syntax) and syntax[pos].isspace():pos+=1
  if pos<len(syntax) and syntax[pos]=='*':pos+=1
  target=group(syntax,pos)
  if target:register,end=target
  else:
   while pos<len(syntax) and syntax[pos].isspace():pos+=1
   if pos>=len(syntax) or syntax[pos]!='\\':continue
   register_name,end=_command(syntax,pos);register='\\'+register_name
  if register.strip()!='\\arraystretch':continue
  pos=end;signature=[]
  if name not in {'def','gdef'}:
   for _ in range(2):
    optional=group(syntax,pos,'[',']')
    if optional:arg,pos=optional;signature.append(arg.strip())
  if signature not in ([],['0']):continue
  body=group(syntax,pos)
  if not body:continue
  value,end=body
  if not re.fullmatch(r'\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*',value):continue
  definition_end=_definition_end(syntax,marker.end(),name)
  if definition_end!=end:continue
  # Suppress only this head; an enclosing unused definition remains masked.
  shielded=active[:marker.start()+1]+'Z'*len(name)+active[marker.end():]
  if masked_tex(shielded)[marker.start()]!='\\':continue
  records.append({'command':name,'value_text':value.strip(),'value':float(value),
                  'start':marker.start(),'end':end,'line':line_at(source,marker.start())})
 return records

def emergency_stretch_end(s:str,i:int,*,opaque_commands:frozenset[str]=frozenset())->int|None:
 """End of an unshadowed primitive assignment to a literal known dimension.

 Only this standard paragraph register has direct-assignment recognition.
 Consume the dimension, never following words, signs, values or macros. Dynamic
 expressions and unknown units/registers remain visible to the guards.
 """
 if 'emergencystretch' in opaque_commands:return None
 number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
 registers='|'.join(sorted(LAYOUT_LENGTH_REGISTERS))
 dimension=r'(?:'+number+r'\s*(?:pt|em|ex|cm|mm|in|bp|pc|dd|cc|sp)(?![A-Za-z@])|(?:'+number+r'\s*)?\\(?:'+registers+r')(?![A-Za-z@]))'
 value=re.match(r'\s*(?:=\s*)?'+dimension,s[i:])
 if value is None:return None
 if any(name in opaque_commands for name in re.findall(r'\\([A-Za-z@]+)',value.group())):return None
 return i+value.end()

def standard_group_offsets(s:str,*,opaque_commands:frozenset[str]=frozenset(),allowed_commands:frozenset[str]|None=None)->frozenset[int]:
 """Balanced standard scopes with no unknown/stateful command inside them.

 Inspect complete active source before declaration/math masking: moving a
 definition or aftergroup into a scope can change later expansion. This does
 not expand macros or establish semantic equivalence.
 """
 if {'begingroup','endgroup'} & opaque_commands:return frozenset()
 stack=[];paired=set();i=0;unmatched=False
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,end=_command(s,i)
  star=end<len(s) and s[end]=='*'
  if name in {'setlength','addtolength'}:
   stop=layout_length_end(s,end,opaque_commands=opaque_commands) if not star and name not in opaque_commands else None
   if stop is not None:i=stop;continue
   for scope in stack:scope[1]=False
   i=opaque_length_args_end(s,end+int(star));continue
  if name in LAYOUT_LENGTH_REGISTERS and not star:
   stop=layout_assignment_end(s,end,name,opaque_commands=opaque_commands)
   if stop is not None:i=stop;continue
  if name=='begingroup' and not star:stack.append([i,True])
  elif name=='endgroup' and not star:
   if stack:
    start,known=stack.pop()
    if known:paired.update((start,i))
   else:unmatched=True
  elif name in opaque_commands or (allowed_commands is not None and name not in allowed_commands) or (star and name in {'begingroup','endgroup'}|TEXT_SPACE_COMMANDS):
   for scope in stack:scope[1]=False
  i=end
 # An incomplete outer scope is unsupported, including its nested pairs.
 return frozenset() if stack or unmatched else frozenset(paired)

def minipage_layout_end(s:str,i:int)->int|None:
 """End of known minipage presentation arguments; unknown forms stay opaque."""
 options=[];pos=i
 for _ in range(3):
  while pos<len(s) and s[pos].isspace():pos+=1
  if pos>=len(s) or s[pos]!='[':break
  value,end=_group(s,pos,'[',']')
  if end==pos:return None
  options.append(value.strip());pos=end
 if options and options[0] not in {'t','c','b'}:return None
 if len(options)>1 and options[1] and not _literal_dimension(options[1]):return None
 if len(options)>2 and options[2] not in {'t','c','b','s'}:return None
 width,end=_group(s,pos)
 return end if end>pos and _literal_dimension(width) else None

def literal_long_identifier(value:str)->bool:
 # nolinkurl has special TeX character rules. Normalize only the literal
 # alphanumeric long identifiers used by D1; macros/URL punctuation stay opaque.
 return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9]{39,}',value))

def shadowed_literal_url_commands(source:str)->frozenset[str]:
 # Direct definitions of these newly supported wrappers invalidate their
 # standard presentation meaning. Inspect the whole source, not a body chunk.
 names=set()
 for record in _macro_definitions(active_source(source)):
  target=record[1] if record[0] in {'def','gdef','edef','xdef','let'} else record[2]
  name=target.lstrip('\\')
  if name in {'nolinkurl','urlstyle'}:names.add(name)
 return frozenset(names)

def _shadowed_presentation_commands(source:str)->frozenset[str]:
 # Newly normalized controls must keep their literal invocation when the
 # source directly defines them. This is not macro expansion or alias tracing.
 names=set(shadowed_literal_url_commands(source))
 presentation_targets=DELIMITER_SIZE_COMMANDS|LAYOUT_LENGTH_REGISTERS|TEXT_SPACE_COMMANDS|{'parbox','setlength','addtolength','begingroup','endgroup','dimexpr','relax'}
 for record in _macro_definitions(source):
  target=record[1] if record[0] in {'def','gdef','edef','xdef','let'} else record[2]
  name=target.lstrip('\\')
  if name in presentation_targets:names.add(name)
 # Inspect literal targets throughout active source, including inside outer
 # macro bodies skipped by the declaration inventory. A later invocation may
 # activate such a nested definition. This deliberately does not expand it.
 # Constructed names remain conservatively opaque instead of being guessed.
 literal_definitions=REGISTER_DECLARATIONS|{'def','gdef','edef','xdef','let','newcommand','renewcommand','providecommand','DeclareRobustCommand','DeclareMathOperator'}
 i=0
 while i<len(source):
  if source[i]!='\\':i+=1;continue
  name,end=_command(source,i)
  if name in {'csname','catcode'}:names.update(presentation_targets)
  if name in literal_definitions:
   pos=end
   if pos<len(source) and source[pos]=='*':pos+=1
   while pos<len(source) and source[pos].isspace():pos+=1
   target,last=_group(source,pos)
   if last==pos:
    if pos<len(source) and source[pos]=='\\':target,last=_command(source,pos)
   if target.strip().lstrip('\\') in presentation_targets:names.add(target.strip().lstrip('\\'))
  i=end
 return frozenset(names)

def _shadowed_literal_url_payload(source:str)->tuple[Any,...]:
 shadowed=shadowed_literal_url_commands(source);records=[];i=0
 while i<len(source):
  if source[i]!='\\':i+=1;continue
  name,end=_command(source,i)
  if name in shadowed:
   star=end<len(source) and source[end]=='*';pos=end+int(star)
   arg,last=_group(source,pos)
   records.append((name,star,re.sub(r'\s+',' ',arg).strip() if last>pos else None))
   i=last if last>pos else pos;continue
  i=end
 return tuple(records)

def active_source(s:str)->str:
 chars=list(s);i=0
 while i<len(s):
  if s[i]=='\\':
   name,j=_command(s,i)
   if name=='verb':
    if j<len(s) and s[j]=='*':j+=1
    if j<len(s):
     end=s.find(s[j],j+1);end=len(s) if end<0 else end+1
     for k in range(i,end):chars[k]='\n' if s[k]=='\n' else ' '
     i=end;continue
   if name=='begin':
    env,end=_group(s,j)
    if env in {'verbatim','Verbatim','lstlisting','minted','comment','filecontents','filecontents*'}:
     marker='\\end{'+env+'}';endpos=s.find(marker,end);stop=len(s) if endpos<0 else endpos+len(marker)
     for k in range(i,stop):chars[k]='\n' if s[k]=='\n' else ' '
     i=stop;continue
   i=j;continue
  if s[i]=='%':
   end=s.find('\n',i);end=len(s) if end<0 else end
   for k in range(i,end):chars[k]=' '
   i=end;continue
  i+=1
 return ''.join(chars)

def _envs(s:str)->List[Dict[str,Any]]:
 stack=[];out=[]
 for m in re.finditer(r'\\(begin|end)\s*\{([^{}]+)\}',s):
  if m.group(1)=='begin':stack.append((m.group(2),m.start(),m.end()))
  elif stack and stack[-1][0]==m.group(2):
   name,start,body=stack.pop();out.append({'name':name,'start':start,'body':body,'tail':m.start(),'end':m.end()})
 return sorted(out,key=lambda x:x['start'])

def _math_like_environment(name:str)->bool:
 return bool(re.search(r'(?:math|equation|eqn|align|gather|multline)',name,re.I))

def _math_spans(s:str,envs:List[Dict[str,Any]])->List[Dict[str,Any]]:
 spans=[]
 for e in envs:
  if (e['name'] in MATH_ENVS or _math_like_environment(e['name'])) and not any(p['start']<=e['start']<p['end'] for p in spans):
   spans.append({**e,'text':s[e['body']:e['tail']],'opaque':e['name'] not in MATH_ENVS,'numbered':e['name'] not in {'math','displaymath','IEEEeqnarraybox','IEEEeqnarraybox*'} and not e['name'].endswith('*')})
 i=0
 while i<len(s):
  cover=next((e for e in spans if e['start']<=i<e['end']),None)
  if cover:i=cover['end'];continue
  if s[i]=='\\':
   name,j=_command(s,i)
   if name=='ensuremath':
    arg,end=_group(s,j)
    if end>j:
     spans.append({'start':i,'end':end,'text':arg,'numbered':False});i=end;continue
   if name in {'(','['}:
    endmark='\\'+(')' if name=='(' else ']');end=s.find(endmark,j)
    if end>=0:spans.append({'start':i,'end':end+2,'text':s[j:end],'numbered':False});i=end+2;continue
   i=j;continue
  if s[i]=='$':
   delim='$$' if s.startswith('$$',i) else '$';j=i+len(delim);k=j
   while k<len(s):
    if s[k]=='\\':_,k=_command(s,k);continue
    if s.startswith(delim,k):break
    k+=1
   if k<len(s):spans.append({'start':i,'end':k+len(delim),'text':s[j:k],'numbered':False});i=k+len(delim);continue
  i+=1
 ordered=sorted(spans,key=lambda x:(x['start'],-x['end']));outer=[]
 for span in ordered:
  if not any(parent['start']<=span['start'] and span['end']<=parent['end'] for parent in outer):outer.append(span)
 return outer

def _roman_stack(s:str)->str:
 # Only the existing bounded long-roman-identifier presentation rewrite.
 pattern=r'_\{\\substack\{((?:\\mathrm\{[A-Za-z][A-Za-z0-9]*\})(?:\\\\\s*\\mathrm\{[A-Za-z0-9]+\})+)\}\}'
 def replace(m):
  chunks=re.findall(r'\\mathrm\{([A-Za-z0-9]+)\}',m.group(1));word=''.join(chunks)
  return '_{\\mathrm{'+word+'}}' if len(word)>=40 else m.group(0)
 return re.sub(pattern,replace,s)

def _presentation_delimiter(s:str,i:int,name:str)->Tuple[str,int]|None:
 while i<len(s) and s[i].isspace():i+=1
 if i>=len(s):return None
 if s[i] in '()[]|':return s[i],i+1
 # The dot is an invisible delimiter only in explicit left/right syntax.
 if s[i]=='.' and name in {'left','right'}:return '',i+1
 if s[i]=='\\':
  delimiter,end=_command(s,i)
  if delimiter in DELIMITER_COMMANDS:return '\\'+delimiter,end
 return None

def _math_tokens(s:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[str,...]:
 s=_roman_stack(s)
 s=re.sub(r'([_^])\{([A-Za-z0-9]|\\[A-Za-z]+)\}',r'\1\2',s)
 s=re.sub(r'(?<=[=+\-<>])\{\}', '', s)
 out=[];i=0;protected=[]
 while i<len(s):
  if s[i].isspace():i+=1;continue
  if s[i]=='\\':
   name,j=_command(s,i)
   if not protected and name in DELIMITER_SIZE_COMMANDS and name not in opaque_commands:
    delimiter=_presentation_delimiter(s,j,name)
    if delimiter is not None:
     token,end=delimiter
     if token:out.append(token)
     i=end;continue
   if name=='ensuremath' and not protected:
    arg,end=_group(s,j)
    if end>j:out.extend(_math_tokens(arg,opaque_commands=opaque_commands));i=end;continue
   if name in {'begin','end'}:
    env,k=_group(s,j)
    if env in MATH_WRAPPERS:
     if name=='begin':
      k=_optional(s,k)
      if env in {'multlined'}:k=_optional(s,k)
      if env in {'alignat','alignat*','alignedat'}:_,k=_group(s,k)
     i=k;continue
    if name=='begin':protected.append(env)
    elif protected and protected[-1]==env:protected.pop()
   if not protected and name not in opaque_commands and name in {'\\','quad','qquad',',','!',';',':','enspace','thinspace','displaystyle','textstyle','scriptstyle','scriptscriptstyle'}:
    i=_optional(s,j) if name=='\\' else j;continue
   if name in {'text','textnormal','textrm','textbf','textit','texttt','operatorname','mathrm'}:
    arg,end=_group(s,j)
    if end>j:
     out.extend(('\\'+name,'{',re.sub(r'\s+', ' ', arg).strip(),'}'));i=end;continue
   if name in {'hspace','vspace'}:
    if j<len(s) and s[j]=='*':j+=1
    _,k=_group(s,j);i=k if k>j else j;continue
   out.append('\\'+name);i=j;continue
  if s[i]=='&' and not protected:i+=1;continue
  out.append(s[i]);i+=1
 return tuple(out)

def _safe_caption_setup(argument:str)->bool:
 fonts={'tiny','scriptsize','footnotesize','small','normalsize','large','Large','LARGE',
        'huge','Huge','bf','it','sl','sc','sf','rm','tt','md','up','normalfont'}
 for field in _split_top(argument,','):
  if '=' not in field:return False
  key,value=field.split('=',1);key=key.strip();value=value.strip()
  if value.startswith('{'):
   body,end=_group(value,0)
   if end==len(value):value=body.strip()
  if key in {'font','labelfont','textfont'}:
   if not set(v.strip() for v in value.split(','))<=fonts:return False
  elif key in {'skip','aboveskip','belowskip'}:
   if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|em|ex|cm|mm|in)',value):return False
  else:return False
 return True

def _safe_float_counter(name:str,value:str)->bool:
 return name.strip() in {'topnumber','bottomnumber','totalnumber','dbltopnumber'} and bool(re.fullmatch(r'\s*\d+\s*',value))

def _strip_layout(s:str,*,text_wrappers:bool=False,opaque_commands:frozenset[str]=frozenset())->str:
 s=re.sub(r'\\(?:widowpenalty|clubpenalty|displaywidowpenalty|brokenpenalty|interlinepenalty)\s*(?:=\s*)?[+-]?\d+\b','',s)
 s=re.sub(r'\\(?:linebreak|nolinebreak)\s*(?:\[[0-4]\])?','',s)
 out=[];i=0
 while i<len(s):
  if s[i]!='\\':out.append(s[i]);i+=1;continue
  name,j=_command(s,i);star=j<len(s) and s[j]=='*';j+=int(star)
  if name in {'setlength','addtolength'}:
   end=layout_length_end(s,j,opaque_commands=opaque_commands) if not star and name not in opaque_commands else None
   if end is not None:i=end;continue
   end=opaque_length_args_end(s,j);out.append(s[i:end]);i=end;continue
  if name in opaque_commands:
   # The complete source has directly redefined this presentation command.
   # Preserve its literal invocation in each caption/cell/argument context,
   # including nested arguments; an unanchored inventory alone misses moves.
   last=j
   while True:
    pos=last
    while pos<len(s) and s[pos].isspace():pos+=1
    if pos<len(s) and s[pos] in '[{':
     opening=s[pos];_,end=_group(s,pos,opening,']' if opening=='[' else '}')
     if end>pos:last=end;continue
    break
   last=max(last,opaque_layout_child_end(s,last))
   out.append(s[i:last]);i=last;continue
  opt=_optional(s,j)
  if name in LAYOUT_LENGTH_REGISTERS and not star:
   end=layout_assignment_end(s,j,name,opaque_commands=opaque_commands)
   if end is not None:i=end;continue
  if name in TEXT_SPACE_COMMANDS and not star:
   # These controls take no arguments: a following group remains body text.
   out.append(' ');i=j;continue
  if name=='looseness' and not star:
   # Only the bounded literal paragraph adjustments used by A1 are layout.
   # Dynamic values and larger changes remain visible to preservation checks.
   value=re.match(r'\s*(?:=\s*)?([+-]?[01])(?=\s|}|$)',s[j:])
   if value:i=j+value.end();continue
  if name in FONT_DECL|RULES:
   if name=='par' and text_wrappers:out.append(' ')
   i=opt;continue
  if name=='urlstyle' and not star:
   body,end=_group(s,j)
   if end>j and body=='same':i=end;continue
  if name=='captionsetup':
   body,end=_group(s,opt)
   if end>opt and _safe_caption_setup(body):i=end;continue
  if name=='setcounter':
   counter,k=_group(s,j);value,end=_group(s,k)
   if end>k and _safe_float_counter(counter,value):i=end;continue
  if name in {'renewcommand','newcommand','providecommand'}:
   arg,k=_group(s,j);k=_optional(s,k);_,end=_group(s,k)
   if arg in {'\\arraystretch','\\topfraction','\\bottomfraction','\\textfraction','\\floatpagefraction','\\dbltopfraction','\\dblfloatpagefraction'} and end>k:i=end;continue
  if name in {'usepackage','RequirePackage','documentclass','bibliographystyle'}:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in LAYOUT_ONE:
   _,end=_group(s,opt);i=end if end>opt else opt
   if name=='fontsize':_,i=_group(s,i)
   continue
  if name=='parbox' and not star:
   # The standard [pos][height][inner-pos]{width}{body} header is layout.
   # Dynamic lengths and unsupported options retain their literal payload.
   stop=minipage_layout_end(s,j)
   if stop is not None:
    pos=stop
    while pos<len(s) and s[pos].isspace():pos+=1
    body,end=_group(s,pos)
    if pos<len(s) and s[pos]=='{' and end>pos:out.append(_strip_layout(body,text_wrappers=text_wrappers,opaque_commands=opaque_commands));i=end;continue
   out.append(s[i:j]);i=j;continue
  if name in {'resizebox','scalebox','rotatebox','raisebox'}:
   _,k=_group(s,opt)
   if name=='resizebox':_,k=_group(s,k)
   if name=='raisebox':k=_optional(s,k);k=_optional(s,k)
   body,end=_group(s,k)
   if end>k:out.append(_strip_layout(body,text_wrappers=text_wrappers,opaque_commands=opaque_commands));i=end;continue
  if name in {'includegraphics'}:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in {'begin','end'}:
   env,end=_group(s,j)
   if name=='begin' and env=='minipage':
    stop=minipage_layout_end(s,end)
    if stop is not None:i=stop;continue
    # Do not discard a dynamic or malformed environment header.
    out.append(s[i:end]);i=end;continue
   if env and env not in TABLE_ENVS:
    i=end
    if name=='begin' and env in FLOAT_ENVS:
     i=_optional(s,i)
     if env in {'wrapfigure','wraptable'}:_,i=_group(s,i);_,i=_group(s,i)
    continue
  if name in PROTECTED_ARGS and not text_wrappers:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in ID_COMMANDS:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in {'footnotemark'}:i=opt;continue
  if name in {'footnotetext','footnote'}:out.append('\\'+name);i=opt;continue
  if text_wrappers and name=='makecell':
   # Standard makecell alignment only changes presentation. Keep dynamic or
   # complex column specifications (and unsupported starred forms) opaque.
   pos=j
   while pos<len(s) and s[pos].isspace():pos+=1
   known=not star
   if pos<len(s) and s[pos]=='[':
    alignment,end=_group(s,pos,'[',']')
    known=known and end>pos and bool(re.fullmatch(r'(?:[lcrtb]|[tb][lcr]|[lcr][tb]|cc)',alignment))
    if end>pos:pos=end
   while pos<len(s) and s[pos].isspace():pos+=1
   body,end=_group(s,pos)
   if end>pos:
    out.append(_strip_layout(body,text_wrappers=True,opaque_commands=opaque_commands) if known else s[i:end])
    i=end;continue
   out.append(s[i:j]);i=j;continue
  if name=='nolinkurl':
   body,end=_group(s,j)
   if end>j:
    out.append(body if not star and literal_long_identifier(body) else s[i:end])
    i=end;continue
   out.append(s[i:j]);i=j;continue
  if text_wrappers and name in TEXT_WRAPPERS|{'shortstack'}:
   body,end=_group(s,opt)
   if end>opt:out.append(_strip_layout(body,text_wrappers=True,opaque_commands=opaque_commands));i=end;continue
  if text_wrappers and name in {'multirow','multicolumn'}:
   span,k=_group(s,opt);spec,k=_group(s,k);body,end=_group(s,k)
   if end>k:
    insertion=_colspec_payload(spec,opaque_commands=opaque_commands) if name=='multicolumn' else ()
    marker='\\'+name+'{'+span+'}'
    if insertion:marker+=repr(insertion)
    out.append(marker+'{'+_strip_layout(body,text_wrappers=True,opaque_commands=opaque_commands)+'}');i=end;continue
  if text_wrappers and name=='\\':i=opt;continue
  # A known argumentless primitive keeps the following assignment independent.
  # All unsupported readers/macros preserve an adjacent layout-token argument.
  end=opaque_layout_child_end(s,j) if name not in {'relax','selectfont','begingroup','endgroup'} else j
  out.append(s[i:end]);i=end
 return ''.join(out)

def _text_tokens(s:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 s=_strip_layout(s,text_wrappers=True,opaque_commands=opaque_commands);envs=_envs(s);math=_math_spans(s,envs);out=[];cursor=0
 for m in math:
  out.extend(c for c in s[cursor:m['start']] if not c.isspace());out.append(('math',_math_tokens(m['text'],opaque_commands=opaque_commands)));cursor=m['end']
 out.extend(c for c in s[cursor:] if not c.isspace())
 while len(out)>=2 and out[0]=='{' and out[-1]=='}':
  depth=0;whole=True
  for i,t in enumerate(out):
   if t=='{':depth+=1
   elif t=='}':depth-=1
   if depth==0 and i<len(out)-1:whole=False;break
  if not whole:break
  out=out[1:-1]
 return tuple(out)

def _colspec_payload(spec:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 # Column alignment/width are layout; @/! insertions can render scientific text.
 out=[];i=0;column=0
 while i<len(spec):
  c=spec[i]
  if c.isspace() or c in '|:':i+=1;continue
  if c in 'lcrX':column+=1;i+=1;continue
  if c in 'pmb':column+=1;_,i=_group(spec,i+1);continue
  if c in '@!><':
   body,end=_group(spec,i+1)
   if end==i+1:out.append((column,c));i+=1;continue
   body=re.sub(r'\\extracolsep\s*\{\s*\\fill\s*\}', '', body)
   payload=_text_tokens(body,opaque_commands=opaque_commands)
   if payload:out.append((column,c,payload))
   i=end;continue
  if c=='*':
   count,j=_group(spec,i+1);body,end=_group(spec,j)
   if count.isdigit() and end>j and int(count)<=100:
    # Preserve insertions in repeated column specs without treating widths as data.
    repeated=_colspec_payload(body,opaque_commands=opaque_commands)
    if repeated:out.append((column,'repeat',count,repeated))
    column+=int(count)*next((v[1] for v in repeated if v[0]=='columns'),0);i=end;continue
  if c=='\\':name,j=_command(spec,i);out.append((column,'unknown','\\'+name));i=j;continue
  out.append((column,'unknown',c));i+=1
 return (('columns',column),)+tuple(out)

def _table_rows(body:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 return tuple(tuple(_text_tokens(cell,opaque_commands=opaque_commands) for cell in _split_top(row,'&'))
              for row in _split_top(body,'\\\\') if _text_tokens(row,opaque_commands=opaque_commands))

def _split_top(s:str,delimiter:str)->List[str]:
 parts=[];start=0;depth=0;i=0
 while i<len(s):
  if depth==0 and s.startswith(delimiter,i):
   parts.append(s[start:i]);i+=len(delimiter)
   if delimiter=='\\\\':i=_optional(s,i)
   start=i;continue
  if s[i]=='\\':_,i=_command(s,i);continue
  if s[i]=='{':depth+=1
  elif s[i]=='}':depth-=1
  i+=1
 parts.append(s[start:]);return parts

def _ids(s:str)->Tuple[Any,...]:
 facts=[];i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name in ID_COMMANDS:
   if j<len(s) and s[j]=='*':j+=1
   j=_optional(s,j);j=_optional(s,j);arg,end=_group(s,j)
   if end>j:
    family='cite' if name.startswith('cite') or name=='nocite' else 'ref' if name!='label' else 'label'
    values=arg.split(',') if family in {'cite','ref'} else [arg]
    facts.extend((family,v.strip()) for v in values);i=end;continue
  i=j
 return tuple(sorted(Counter(facts).items()))

def _configuration_payload(s:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 records=[];i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name=='captionsetup':
   j=_optional(s,j);arg,end=_group(s,j)
   if end>j:
    if not _safe_caption_setup(arg):records.append((name,_text_tokens(arg,opaque_commands=opaque_commands)))
    i=end;continue
  if name=='setcounter':
   arg,k=_group(s,j);value,end=_group(s,k)
   if end>k:
    if not _safe_float_counter(arg,value):records.append((name,arg,_text_tokens(value,opaque_commands=opaque_commands)))
    i=end;continue
  i=j
 return tuple(records)

def _macro_definitions(s:str)->Tuple[Any,...]:
 # No expansion or guessed equivalence. Changing an unknown declaration is a
 # review/rollback boundary, even when the formula using it stays unchanged.
 layout_macros={'\\arraystretch','\\topfraction','\\bottomfraction','\\textfraction',
                '\\floatpagefraction','\\dbltopfraction','\\dblfloatpagefraction'}
 declarations={'newcommand','renewcommand','providecommand','DeclareRobustCommand',
               'DeclareMathOperator','newenvironment','renewenvironment','provideenvironment',
               'newcolumntype'}
 primitives={'def','gdef','edef','xdef'}
 records=[];i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name in REGISTER_DECLARATIONS:
   pos=j
   while pos<len(s) and s[pos].isspace():pos+=1
   target,end=_group(s,pos)
   if end==pos:
    if pos<len(s) and s[pos]=='\\':target_name,end=_command(s,pos);target='\\'+target_name
   if target.strip().lstrip('\\') in TEXT_SPACE_COMMANDS|{'emergencystretch'}:
    # Even an unused replacement of a newly normalized standard control is an
    # unsupported declaration change requiring review. Record literal aliases;
    # this does not evaluate register numbers or expand their value macros.
    value='';last=end
    if name.endswith('def'):
     while last<len(s) and (s[last].isspace() or s[last]=='='):last+=1
     if last<len(s) and s[last]=='\\':_,stop=_command(s,last)
     else:
      match=re.match(r'[+-]?\d+',s[last:]);stop=last+match.end() if match else min(last+1,len(s))
     value=s[last:stop];last=stop
    records.append((name,False,target.strip(),(),(value,)))
    i=last;continue
  if name in declarations:
   star=j<len(s) and s[j]=='*';j+=int(star)
   while j<len(s) and s[j].isspace():j+=1
   target,end=_group(s,j)
   if end==j:
    k=j
    while k<len(s) and s[k].isspace():k+=1
    if k<len(s) and s[k]=='\\':target_name,end=_command(s,k);target='\\'+target_name
   k=end;signature=[]
   for _ in range(2):
    pos=k
    while pos<len(s) and s[pos].isspace():pos+=1
    if pos<len(s) and s[pos]=='[':arg,k=_group(s,pos,'[',']');signature.append(arg)
   body,end=_group(s,k)
   if end>k:
    bodies=[body]
    if name.endswith('environment'):
     tail,last=_group(s,end)
     if last>end:bodies.append(tail);end=last
    known_layout = target in layout_macros and (not signature or [arg.strip() for arg in signature]==['0']) and len(bodies)==1 and re.fullmatch(r'\s*(?:\d+(?:\.\d*)?|\.\d+)\s*',body)
    if not known_layout:
     records.append((name,star,target,tuple(signature),tuple(re.sub(r'\s+',' ',v).strip() for v in bodies)))
    i=end;continue
  if name in primitives:
   k=j
   while k<len(s) and s[k].isspace():k+=1
   if k<len(s) and s[k]=='\\':
    target,end=_command(s,k);brace=s.find('{',end)
    if brace>=0:
     body,last=_group(s,brace)
     if last>brace:
      known_layout = '\\'+target in layout_macros and not s[end:brace].strip() and re.fullmatch(r'\s*(?:\d+(?:\.\d*)?|\.\d+)\s*',body)
      if not known_layout:
       records.append((name,'\\'+target,s[end:brace].strip(),re.sub(r'\s+',' ',body).strip()))
      i=last;continue
  if name=='let':
   k=j
   while k<len(s) and s[k].isspace():k+=1
   if k<len(s) and s[k]=='\\':
    target,end=_command(s,k);k=end
    while k<len(s) and (s[k].isspace() or s[k]=='='):k+=1
    value,last=_command(s,k) if k<len(s) and s[k]=='\\' else (s[k:k+1],k+1)
    records.append((name,target,value));i=last;continue
  i=j
 return tuple(records)

def _payload(s:str)->Dict[str,Any]:
 s=active_source(s);opaque_commands=_shadowed_presentation_commands(s);envs=_envs(s);floats=[e for e in envs if e['name'] in FLOAT_ENVS];tables=[e for e in envs if e['name'] in TABLE_ENVS];math=_math_spans(s,envs)
 def context(start):
  parents=[e for e in floats+[t for t in tables if t['name']=='longtable'] if e['start']<=start<e['end']]
  if not parents:return None
  parent=min(parents,key=lambda e:e['end']-e['start']);block=s[parent['body']:parent['tail']]
  labels=tuple(re.findall(r'\\label\s*\{([^{}]+)\}',block));graphics=tuple(re.findall(r'\\includegraphics(?:\[[^\]]*\])?\s*\{([^{}]+)\}',block))
  return (labels,graphics)
 math_body=[];math_float=[]
 for m in math:
  record=('opaque_math_environment',m['name'],re.sub(r'\s+',' ',m['text']).strip()) if m.get('opaque') else (m['numbered'],_math_tokens(m['text'],opaque_commands=opaque_commands));key=context(m['start'])
  if key is None:math_body.append(record)
  else:math_float.append((key,record))
 table_records=[]
 for e in tables:
  body=s[e['body']:e['tail']];offset=_optional(body,0);spec=''
  for _ in range(2 if e['name'] in {'tabular*','tabularx'} else 1):spec,offset=_group(body,offset)
  body=body[offset:]
  body=re.sub(r'\\(?:cmidrule|cline)(?:\[[^\]]*\])?\{[^{}]*\}','',body)
  body=re.sub(r'\\(?:toprule|midrule|bottomrule|hline)(?:\[[^\]]*\])?','',body)
  key=context(e['start'])
  if e['name']=='longtable':
   # A generated repeated head is removable only if it exactly matches the first
   # head's data. Unknown footer text remains protected rather than discarded.
   match=re.search(r'\\endfirsthead(.*?)\\endhead(.*?)\\endfoot(.*?)\\endlastfoot',body,re.S)
   if match:
    first=body[:match.start()]
    caption=re.search(r'\\caption(?:\[[^\]]*\])?\s*\{',first)
    labels=tuple(re.findall(r'\\label\s*\{([^{}]+)\}',first))
    if key is None and labels:key=(labels,())
    if caption:
     _,caption_end=_group(first,caption.end()-1)
     first=first[:caption.start()]+first[caption_end:]
    first_rows=_table_rows(first,opaque_commands=opaque_commands);repeat_rows=_table_rows(match.group(1),opaque_commands=opaque_commands)
    footer=_text_tokens(match.group(2),opaque_commands=opaque_commands);last_footer=_text_tokens(match.group(3),opaque_commands=opaque_commands)
    generated_footer=re.fullmatch(r'\s*\\multicolumn\{\d+\}\{r\}\{Continued on next page\}\s*(?:\\\\)?\s*',match.group(2))
    if repeat_rows!=first_rows:table_records.append((key,('repeated_head',repeat_rows)))
    if footer and not generated_footer:table_records.append((key,('footer',footer)))
    if last_footer:table_records.append((key,('last_footer',last_footer)))
    body=first+body[match.end():]
  rows=_table_rows(body,opaque_commands=opaque_commands)
  table_records.append((key,rows,_colspec_payload(spec,opaque_commands=opaque_commands)))
 # Numeric payload outside protected math/table blocks, preserving body order.
 excluded=sorted([(e['start'],e['end']) for e in tables]+[(m['start'],m['end']) for m in math])
 chars=list(s)
 for start,end in excluded:chars[start:end]=' '*(end-start)
 remainder=''.join(chars);numeric_body=[];numeric_float=[];arguments=[]
 def numbers(text):return tuple(re.findall(r'(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',_strip_layout(text,opaque_commands=opaque_commands)))
 body_parts=[];cursor=0
 for e in floats:
  if e['start']<cursor:continue
  body_parts.append(remainder[cursor:e['start']]);values=numbers(remainder[e['start']:e['end']])
  if values:numeric_float.append((context(e['start']),values))
  cursor=e['end']
 body_parts.append(remainder[cursor:]);numeric_body=numbers(''.join(body_parts))
 i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name in PROTECTED_ARGS:
   j=_optional(s,j);arg,end=_group(s,j)
   if end>j:arguments.append((context(i),name,_text_tokens(arg,opaque_commands=opaque_commands)));i=end;continue
  i=j
 return {'configuration_arguments':_configuration_payload(s,opaque_commands=opaque_commands),'macro_definitions':_macro_definitions(s),'shadowed_literal_url_invocations':_shadowed_literal_url_payload(s),'math_body':tuple(math_body),'math_floats':tuple(sorted(Counter(math_float).items(),key=repr)),'tables':tuple(sorted(Counter(table_records).items(),key=repr)),'identifiers':_ids(s),'numbers_body':numeric_body,'numbers_floats':tuple(sorted(Counter(numeric_float).items(),key=repr)),'protected_arguments':tuple(sorted(Counter(arguments).items(),key=repr))}

def scientific_payload_diff(original:str,repaired:str)->Dict[str,Any]:
 limits='Conservative token invariants, not mathematical equivalence; No macro expansion or mathematical equivalence proof; exotic TeX definitions/restructuring require host review. Cross-file checking requires callers to compare every modified source file.'
 def summary(value):
  return {'sha256':hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest(),'component_counts':{k:len(v) for k,v in value.items()}}
 try:
  before=_payload(original);after=_payload(repaired);changed=[key for key in before if before[key]!=after[key]]
  before_summary=summary(before);after_summary=summary(after)
 except Exception as exc:
  # Checker failure is neither a successful check nor confirmed damage. Keep
  # legacy callers fail closed via checked=False, with a separate diagnosis.
  return {'checked':False,'status':'unavailable','before':None,'after':None,'changed_components':[],
          'regression_reasons':['Scientific checker did not complete: '+type(exc).__name__],
          'assessment':{'classification':'checker_error','scope':'protected_token_invariants',
                        'semantic_damage_proven':False,'requires_host_review':True,
                        'confirmed_changed_components':[],'review_changed_components':[]},
          'limits':limits}
 def supported_math_records(value):
  if isinstance(value,tuple):
   if len(value)==2 and isinstance(value[0],bool):return [value]
   if value and value[0]=='opaque_math_environment':return []
   return [record for item in value for record in supported_math_records(item)]
  return []
 review=[]
 for key in changed:
  if key in {'macro_definitions','configuration_arguments','shadowed_literal_url_invocations'}:
   review.append(key)
  elif key in {'math_body','math_floats'} and supported_math_records(before[key])==supported_math_records(after[key]):
   review.append(key)
 confirmed=[key for key in changed if key not in review]
 # A confirmed literal invariant change is not a mathematical damage proof.
 # Unsupported declarations/opaque environments retain a separate review
 # diagnosis; neither diagnosis relaxes the legacy failed status.
 classification='confirmed_payload_change' if confirmed else 'manual_review' if changed else 'supported_preservation'
 return {'checked':True,'status':'failed' if changed else 'pass','before':before_summary,'after':after_summary,
         'changed_components':changed,'regression_reasons':['Scientific payload changed: '+k for k in changed],
         'assessment':{'classification':classification,'scope':'protected_token_invariants',
                       'semantic_damage_proven':False,'requires_host_review':bool(changed),
                       'confirmed_changed_components':confirmed,'review_changed_components':review},
         'limits':limits}
