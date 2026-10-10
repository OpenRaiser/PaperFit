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
LAYOUT_LENGTH_REGISTERS={'topmargin','oddsidemargin','evensidemargin','marginparsep','marginparwidth','headheight','headsep','footskip','hoffset','voffset','tabcolsep','arrayrulewidth','textheight','textwidth','columnsep','columnwidth','linewidth','abovecaptionskip','belowcaptionskip','floatsep','dblfloatsep','textfloatsep','dbltextfloatsep','bibsep','intextsep','parskip','parindent','baselineskip','emergencystretch','abovedisplayskip','belowdisplayskip','abovedisplayshortskip','belowdisplayshortskip','itemsep','topsep','parsep','partopsep','leftmargin','rightmargin','listparindent','itemindent','labelsep','labelwidth'}
# Only standard glue registers accept stretch/shrink components. Dimension
# registers do not consume trailing prose such as "plus 91.2".
# natbib's standard bibsep length supplies bibliography itemsep. Direct source
# definitions still shadow it, as they do every other recognized register.
LAYOUT_GLUE_REGISTERS={'abovecaptionskip','belowcaptionskip','floatsep','dblfloatsep','textfloatsep','dbltextfloatsep','bibsep','intextsep','parskip','baselineskip','abovedisplayskip','belowdisplayskip','abovedisplayshortskip','belowdisplayshortskip','itemsep','topsep','parsep','partopsep'}
TEXT_SPACE_COMMANDS={'quad','qquad'}
# Standard balance-package controls change the output routine, take no
# arguments, and never consume a following group, word, sign or numeric result.
ZERO_ARG_LAYOUT_COMMANDS={'balance','nobalance'}
REGISTER_DECLARATIONS={'newlength','newdimen','newskip','newmuskip','dimendef','skipdef','muskipdef','countdef','chardef','mathchardef','toksdef'}
DOCUMENT_COMMAND_DECLARATIONS={'NewDocumentCommand','RenewDocumentCommand','ProvideDocumentCommand','DeclareDocumentCommand'}
DOCUMENT_ENVIRONMENT_DECLARATIONS={'NewDocumentEnvironment','RenewDocumentEnvironment','ProvideDocumentEnvironment','DeclareDocumentEnvironment'}

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
 if name in ZERO_ARG_LAYOUT_COMMANDS:
  # An unknown reader can consume this control token as its own argument.
  # Keep it with that invocation rather than normalizing it independently.
  return end
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

def _local_literal_length_definitions(source:str)->frozenset[str]:
 """Known no-argument literal length declarations confined to table/float scope.

 This is not global macro equivalence. Any other definition/alias of the same
 register keeps it opaque throughout the conservative single-file checker.
 """
 from source_map import masked_tex
 envs=_envs(source);local=[e for e in envs if e['name'] in FLOAT_ENVS|TABLE_ENVS]
 candidates={};unsafe=set()
 forms={'newcommand','renewcommand','providecommand'}
 heads=forms|{'def','gdef','edef','xdef','let'}|REGISTER_DECLARATIONS
 declarations=_macro_definitions(source)
 defined={str(r[1] if r[0] in {'def','gdef','edef','xdef','let'} else r[2]).lstrip('\\') for r in declarations}
 if defined & (forms|{'def','begin','end'}):return frozenset()
 conditional_depth=0;conditional_positions=[]
 for marker in re.finditer(r'\\(if[A-Za-z@]*|fi)(?![A-Za-z@])',source):
  if marker[1]=='fi':conditional_depth=max(0,conditional_depth-1)
  else:conditional_depth+=1
  conditional_positions.append((marker.end(),conditional_depth))
 for match in re.finditer(r'\\([A-Za-z@]+)',source):
  name=match[1]
  if name not in heads:continue
  pos=match.end()
  if pos<len(source) and source[pos]=='*':pos+=1
  while pos<len(source) and source[pos].isspace():pos+=1
  target,end=_group(source,pos)
  if end==pos and pos<len(source) and source[pos]=='\\':
   target_name,end=_command(source,pos);target='\\'+target_name
  target=target.strip().lstrip('\\')
  if target not in LAYOUT_LENGTH_REGISTERS:continue
  signature=[]
  pos=end
  for _ in range(2):
   cursor=pos
   while cursor<len(source) and source[cursor].isspace():cursor+=1
   if cursor<len(source) and source[cursor]=='[':
    arg,pos=_group(source,cursor,'[',']');signature.append(arg.strip())
  body,last=_group(source,pos)
  literal=bool(re.fullmatch(r'\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|em|ex|cm|mm|in|bp|pc|dd|cc|sp)\s*',body))
  confined=any(e['body']<=match.start() and last<=e['tail'] for e in local)
  # A nested inactive definition, global prefix or conditional cannot supply
  # the standard local register meaning for other source regions. Shield only
  # this declaration head when testing whether an outer definition masks it.
  shielded=source[:match.start()+1]+'Z'*len(name)+source[match.end():]
  executable=masked_tex(shielded)[match.start()]=='\\'
  global_prefix=bool(re.search(r'\\global(?:\s*\\(?:long|outer|protected))*\s*$',source[:match.start()]))
  conditional=next((depth for end,depth in reversed(conditional_positions) if end<=match.start()),0)>0
  if name in forms|{'def'} and signature in ([],['0']) and last>pos and literal and confined and executable and not global_prefix and not conditional:
   candidates[target]=True
  else:unsafe.add(target)
 return frozenset(set(candidates)-unsafe)


def adjustbox_layout_end(source:str,pos:int,*,opaque_commands:frozenset[str]=frozenset())->int|None:
 """Finite standard size-only adjustbox options; arbitrary keys stay opaque."""
 options,end=_group(source,pos)
 if end<=pos:return None
 keys={'width','height','totalheight','max width','max height','max totalheight',
       'min width','min height','min totalheight'}
 for option in options.split(','):
  key,sep,value=option.partition('=')
  if not sep or key.strip() not in keys:return None
  if not re.fullmatch(r'\s*'+_layout_dimension_pattern()+r'\s*',value):return None
  if not _length_commands_safe(value,opaque_commands):return None
 return end


def fontsize_layout_end(source:str,pos:int)->int|None:
 """A standard fontsize accepts two finite literal point dimensions only."""
 number=r'\s*[+]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt)?\s*'
 first,end=_group(source,pos);second,last=_group(source,end)
 if end<=pos or last<=end:return None
 return last if re.fullmatch(number,first) and re.fullmatch(number,second) else None


def _shadowed_presentation_commands(source:str,*,include_constructed:bool=True)->frozenset[str]:
 # Newly normalized controls must keep their literal invocation when the
 # source directly defines them. This is not macro expansion or alias tracing.
 names=set(shadowed_literal_url_commands(source))
 presentation_targets=MATH_WRAPPERS|DELIMITER_SIZE_COMMANDS|LAYOUT_LENGTH_REGISTERS|TEXT_SPACE_COMMANDS|ZERO_ARG_LAYOUT_COMMANDS|FLOAT_ENVS|FONT_DECL|LAYOUT_ONE|{'parbox','setlength','addtolength','begingroup','endgroup','dimexpr','relax','selectfont','caption','captionof','minipage','adjustbox','includegraphics','label','begin','end','stepcounter','refstepcounter','setcounter','addtocounter'}
 local_lengths=_local_literal_length_definitions(source)
 for record in _macro_definitions(source):
  target=record[1] if record[0] in {'def','gdef','edef','xdef','let'} else record[2]
  name=target.lstrip('\\')
  if name in presentation_targets and name not in local_lengths:names.add(name)
 # Inspect literal targets throughout active source, including inside outer
 # macro bodies skipped by the declaration inventory. A later invocation may
 # activate such a nested definition. This deliberately does not expand it.
 # Constructed names remain conservatively opaque instead of being guessed.
 # Environment declarations also define their same-named begin control, and
 # can be nested inside a later-invoked outer macro just like command forms.
 literal_definitions=REGISTER_DECLARATIONS|DOCUMENT_COMMAND_DECLARATIONS|DOCUMENT_ENVIRONMENT_DECLARATIONS|{'def','gdef','edef','xdef','let','newcommand','renewcommand','providecommand','DeclareRobustCommand','DeclareMathOperator','newenvironment','renewenvironment','provideenvironment'}
 i=0
 while i<len(source):
  if source[i]!='\\':i+=1;continue
  name,end=_command(source,i)
  if include_constructed and name in {'csname','catcode'}:names.update(presentation_targets)
  if name in literal_definitions:
   pos=end
   if pos<len(source) and source[pos]=='*':pos+=1
   while pos<len(source) and source[pos].isspace():pos+=1
   target,last=_group(source,pos)
   if last==pos:
    if pos<len(source) and source[pos]=='\\':target,last=_command(source,pos)
   target_name=target.strip().lstrip('\\')
   if target_name in presentation_targets and target_name not in local_lengths:names.add(target_name)
  i=end
 return frozenset(names)

def presentation_shadow_context(sources)->frozenset[str]:
 """Union literal shadows from the caller's baseline/current source inventory.

 Callers keep their source identities and compare files separately. This only
 supplies missing declaration context; it does not expand or execute TeX.
 """
 return frozenset().union(*(_shadowed_presentation_commands(active_source(source)) for source in sources))

def presentation_dynamic_context(sources)->frozenset[str]:
 """Controls opaque solely because this inventory contains constructed TeX.

 Explicit literal shadows remain excluded: a known declaration emitting #1 or
 text cannot use the dynamic-template uncertainty route.
 """
 active=[active_source(source) for source in sources]
 if not any(re.search(r'\\(?:csname|catcode)(?![A-Za-z@])',source) for source in active):return frozenset()
 shadows=presentation_shadow_context(active)
 literal=frozenset().union(*(_shadowed_presentation_commands(source,include_constructed=False) for source in active))
 return shadows-literal

def project_presentation_shadow_context(project_root,main_tex:str,*,sources=())->frozenset[str]:
 """Read reachable current definitions for standalone project-aware callers.

 Snapshot/executor callers additionally supply their captured baseline texts.
 Installed dependencies are not expanded and no source identities are waived.
 """
 from pathlib import Path
 from runtime_snapshots import discover_source_files, TEX_DEFINITION_EXTENSIONS
 root=Path(project_root).resolve()
 current=[path.read_text(encoding='utf-8',errors='surrogateescape')
          for path in discover_source_files(root,main_tex) if path.suffix.lower() in TEX_DEFINITION_EXTENSIONS]
 return presentation_shadow_context([*sources,*current])

def project_presentation_dynamic_context(project_root,main_tex:str,*,sources=())->frozenset[str]:
 from pathlib import Path
 from runtime_snapshots import discover_source_files, TEX_DEFINITION_EXTENSIONS
 root=Path(project_root).resolve()
 current=[path.read_text(encoding='utf-8',errors='surrogateescape')
          for path in discover_source_files(root,main_tex) if path.suffix.lower() in TEX_DEFINITION_EXTENSIONS]
 return presentation_dynamic_context([*sources,*current])

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
    math_font_environments={'tiny','scriptsize','footnotesize','small','normalsize','large','Large','LARGE','huge','Huge'}
    if not protected and env not in opaque_commands and env in MATH_WRAPPERS|math_font_environments:
     if name=='begin' and env in MATH_WRAPPERS:
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
   if name in {'hspace','vspace'} and name not in opaque_commands:
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

def _caption_at(s:str,start:int,*,family:str|None=None,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]|None:
 """Read the standard caption signatures without expanding any command."""
 from source_map import group as balanced_group
 name,pos=_command(s,start)
 if name not in {'caption','captionof'}:return None
 star=pos<len(s) and s[pos]=='*';pos+=int(star);kind=family;short=None;reason=None
 if name in opaque_commands:reason='shadowed_caption_command'
 if name=='captionof':
  value=balanced_group(s,pos)
  if value is None:reason=reason or 'malformed_caption_type'
  else:
   kind,pos=value
   if kind not in {'figure','table'}:reason=reason or 'unsupported_caption_type'
 cursor=pos
 while cursor<len(s) and s[cursor].isspace():cursor+=1
 if cursor<len(s) and s[cursor]=='[':
  value=balanced_group(s,cursor,'[',']')
  if value is None:reason=reason or 'malformed_short_caption'
  else:short,pos=value
 value=balanced_group(s,pos)
 if value is None:
  newline=s.find('\n',pos)
  text=None;end=len(s) if s[pos:].lstrip().startswith(('{','[')) or newline<0 else newline
  reason=reason or 'malformed_long_caption'
 else:text,end=value
 return {'command':name,'start':start,'end':end,'family':kind,'type':kind,
         'star':star,'short':short,'text':text,'supported':reason is None,
         'reason':reason,'source':s[start:end]}

def standard_caption_declarations(source:str,*,opaque_commands:frozenset[str]=frozenset())->List[Dict[str,Any]]:
 """Position-preserving caption records, including unsupported raw invocations."""
 from source_map import masked_tex
 active=masked_tex(source);envs=_envs(active);shadowed=opaque_commands|_shadowed_presentation_commands(active_source(source));records=[];i=0
 while i<len(active):
  if active[i]!='\\':i+=1;continue
  name,end=_command(active,i)
  if name not in {'caption','captionof'}:i=end;continue
  parents=[e for e in envs if e['name'] in FLOAT_ENVS|{'longtable'} and e['body']<=i<e['tail']]
  parent=min(parents,key=lambda e:e['end']-e['start']) if parents else None
  family=('figure' if 'figure' in parent['name'] else 'table') if parent else None
  record=_caption_at(active,i,family=family,opaque_commands=shadowed)
  records.append(record);i=max(end,record['end'])
 return records

def _caption_payload(caption:Dict[str,Any],*,opaque_commands:frozenset[str]=frozenset(),family:str|None=None)->Tuple[Any,...]:
 """Retain caption fields and the ordered spelling of reference/cite calls."""
 protected=opaque_commands|ID_COMMANDS
 return (family if family is not None else caption['family'],caption['star'],
         None if caption['short'] is None else _text_tokens(caption['short'],opaque_commands=protected),
         _text_tokens(caption['text'],opaque_commands=protected))

def _caption_label_binding(s:str,caption:Dict[str,Any],label_start:int,start:int,end:int,
                           *,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Literal relative order only; never execute or infer a TeX counter value."""
 controls=[];pos=start
 while pos<end:
  if s[pos]!='\\':pos+=1;continue
  name,stop=_command(s,pos)
  if name not in {'stepcounter','refstepcounter','setcounter','addtocounter'}:pos=stop;continue
  star=stop<len(s) and s[stop]=='*';argument=stop+int(star);args=[]
  for _ in range(2 if name in {'setcounter','addtocounter'} else 1):
   value,last=_group(s,argument)
   if last<=argument or last>end:break
   args.append(value.strip());argument=last
  controls.append({'command':name,'star':star,'arguments':args,'source_start':pos,'source_end':argument,
                   'supported':name not in opaque_commands and not star and len(args)==(2 if name in {'setcounter','addtocounter'} else 1),
                   'relation_to_caption':'before' if pos<caption['start'] else 'after',
                   'relation_to_label':'before' if pos<label_start else 'after'})
  pos=max(stop,argument)
 return {'relation':'before_caption' if label_start<caption['start'] else 'after_caption',
         'label_source_start':label_start,'caption_source_start':caption['start'],'counter_controls':controls,
         'limits':'Literal source order and counter-control spelling only; no counter execution or intended-target proof.'}

def _caption_binding_payload(binding:Dict[str,Any])->Tuple[Any,...]:
 return (binding['relation'],tuple((c['command'],c['star'],tuple(c['arguments']),c['supported'],
          c['relation_to_caption'],c['relation_to_label']) for c in binding['counter_controls']))

def standard_layout_object_inventory(source:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Explicit local source ownership, never a rendering or semantic proof.

 Only unique labelled/captioned floats and balanced standard minipage/group
 objects are supported. Incomplete/opaque containers retain raw evidence and
 cannot justify a container-count exemption at a caller's acceptance boundary.
 """
 from source_map import masked_tex
 active=masked_tex(source);envs=_envs(active);shadowed=opaque_commands|_shadowed_presentation_commands(active_source(source))
 floats=[e for e in envs if e['name'] in FLOAT_ENVS];captions=standard_caption_declarations(source,opaque_commands=shadowed)
 scientific_regions=[e for e in envs if e['name'] in TABLE_ENVS]+_math_spans(active,envs)
 labels=[(m.start(),m[1]) for m in re.finditer(r'\\label\s*\{([^{}]+)\}',active)];counts=Counter(label for _,label in labels)
 objects=[];unsupported=[];scopes=[]
 barriers=[];conditionals=[]
 # Local scope ownership cannot be inferred through an unknown argument or
 # conditional. Unchanged opaque content elsewhere does not poison a supported
 # object; the independent raw payload channels still retain that content.
 known_readers=TEXT_WRAPPERS|FONT_DECL|LAYOUT_ONE|ID_COMMANDS|PROTECTED_ARGS|LAYOUT_LENGTH_REGISTERS|{'begin','end','captionof','resizebox','scalebox','rotatebox','raisebox','parbox','setlength','addtolength','captionsetup','setcounter','usepackage','RequirePackage','documentclass','bibliography','bibliographystyle'}
 i=0
 while i<len(active):
  if active[i]!='\\':i+=1;continue
  name,end=_command(active,i)
  if name.startswith('if') and re.fullmatch(r'if[A-Za-z@]*',name):conditionals.append(i)
  elif name=='fi':
   if conditionals:barriers.append({'start':conditionals.pop(),'end':end,'reason':'conditional_scope'})
  if name not in known_readers:
   pos=end+int(end<len(active) and active[end]=='*')
   while True:
    while pos<len(active) and active[pos].isspace():pos+=1
    if pos>=len(active) or active[pos] not in '[{':break
    opening=active[pos];_,last=_group(active,pos,opening,']' if opening=='[' else '}')
    if last<=pos:break
    barriers.append({'start':pos,'end':last,'reason':'opaque_reader_argument'});pos=last
  i=end
 barriers.extend({'start':start,'end':len(active),'reason':'unbalanced_conditional_scope'} for start in conditionals)
 known_outer=FLOAT_ENVS|TABLE_ENVS|MATH_WRAPPERS|{'document','minipage','center','flushleft','flushright','matrix','pmatrix','bmatrix','Bmatrix','vmatrix','Vmatrix','smallmatrix','cases'}
 barriers.extend({'start':e['start'],'end':e['end'],'reason':'unknown_environment_scope'} for e in envs
                 if e['name'] not in known_outer and not (e['name']=='adjustbox' and e['name'] not in shadowed
                 and adjustbox_layout_end(active,e['body'],opaque_commands=shadowed) is not None))
 def in_span(items,start,end,key):return [item for item in items if start<=key(item)<end]
 def header_end(e):
  pos=_optional(active,e['body'])
  if e['name'] in {'wrapfigure','wraptable'}:
   for _ in range(2):
    _,end=_group(active,pos)
    if end<=pos:return None
    pos=end
  return pos
 for e in envs:
  if e['name']=='minipage':
   stop=minipage_layout_end(active,e['body']) if 'minipage' not in shadowed else None
   if stop is None or stop>e['tail']:unsupported.append({'start':e['start'],'end':e['end'],'reason':'unsupported_minipage_header'})
   else:scopes.append({'start':e['start'],'end':e['end'],'body':stop,'tail':e['tail'],'kind':'minipage'})
 for marker in re.finditer(r'\\begin\s*\{([^{}]+)\}',active):
  if marker[1] in FLOAT_ENVS|{'minipage'} and not any(e['start']==marker.start() for e in envs):
   unsupported.append({'start':marker.start(),'end':len(active),'reason':'unbalanced_layout_environment'})
 if not {'begingroup','endgroup'} & shadowed:
  stack=[]
  for marker in re.finditer(r'\\(begingroup|endgroup)(?![A-Za-z@])',active):
   if active[marker.end():marker.end()+1]=='*':unsupported.append({'start':marker.start(),'end':marker.end()+1,'reason':'unsupported_starred_group'})
   if marker[1]=='begingroup':stack.append(marker)
   elif stack:
    first=stack.pop();scopes.append({'start':first.start(),'end':marker.end(),'body':first.end(),'tail':marker.start(),'kind':'standard_group'})
   else:unsupported.append({'start':marker.start(),'end':marker.end(),'reason':'unbalanced_standard_group'})
  unsupported.extend({'start':m.start(),'end':len(active),'reason':'unbalanced_standard_group'} for m in stack)
 elif re.search(r'\\(?:begingroup|endgroup)(?![A-Za-z@])',active):
  unsupported.append({'start':0,'end':len(active),'reason':'shadowed_standard_group'})
 def candidate(scope,parent=None):
  caps=in_span(captions,scope['body'],scope['tail'],lambda c:c['start'])
  ids=in_span(labels,scope['body'],scope['tail'],lambda item:item[0])
  if len(caps)!=1 or len(ids)!=1 or counts[ids[0][1]]!=1:return None
  cap=caps[0]
  if not cap['supported'] or cap['family'] not in {'figure','table'}:return None
  if cap['start']<=ids[0][0]<cap['end']:return None
  if {'label','begin','end'} & shadowed or (parent and parent['name'] in shadowed):return None
  if any((e['start']<=ids[0][0]<e['end'] or e['start']<=cap['start']<e['end'])
         and not (scope['kind']=='longtable' and e['start']==scope['start']) for e in scientific_regions):return None
  if any(scope['start']<=u['start']<scope['end'] or u['start']<=scope['start']<u['end'] for u in unsupported):return None
  obstruction=next((u for u in barriers if (u['start']<=scope['start'] and scope['end']<=u['end']) or (u['start']<=cap['start']<u['end']) or (u['start']<=ids[0][0]<u['end'])
                   or (u['reason'] in {'unknown_environment_scope','conditional_scope','unbalanced_conditional_scope'} and u['start']<scope['tail'] and scope['body']<u['end'])),None)
  if obstruction:
   unsupported.append({**obstruction,'object_label':ids[0][1]});return None
  graphics=[];i=scope['body']
  while i<scope['tail']:
   if active[i]!='\\':i+=1;continue
   name,end=_command(active,i)
   if name!='includegraphics':i=end;continue
   if name in shadowed:return None
   star=end<len(active) and active[end]=='*';pos=end+int(star);optional_end=_optional(active,pos)
   options=active[pos:optional_end].strip();path,last=_group(active,optional_end)
   if last<=optional_end:return None
   graphics.append({'path':path,'options':options,'star':star,'start':i,'end':last});i=last
  return {'label':ids[0][1],'family':cap['family'],'scope_kind':scope['kind'],'start':scope['start'],'end':scope['end'],
          'body':scope['body'],'tail':scope['tail'],'caption':cap,'graphics':graphics,
          'label_binding':_caption_label_binding(active,cap,ids[0][0],scope['body'],scope['tail'],opaque_commands=shadowed),
          'parent_container':{'name':parent['name'],'start':parent['start'],'end':parent['end']} if parent else None}
 for parent in floats:
  nested=[e for e in floats if parent['start']<e['start']<parent['end']]
  if nested:unsupported.append({'start':parent['start'],'end':parent['end'],'reason':'nested_float_ownership'});continue
  stop=header_end(parent)
  scope={**parent,'body':stop if stop is not None else parent['body'],'kind':'float'}
  whole=candidate(scope,parent) if stop is not None else None
  if whole:objects.append(whole);continue
  local=[]
  for child in scopes:
   if parent['body']<=child['start'] and child['end']<=parent['tail']:
    value=candidate(child,parent)
    if value:local.append(value)
  # Choose the innermost complete scope; overlapping different objects are
  # unsupported rather than silently assigned a guessed source owner.
  local=[o for o in local if not any(o['start']<q['start'] and q['end']<o['end'] for q in local)]
  if len({o['label'] for o in local})!=len(local) or any(a['start']<b['start']<a['end'] for a in local for b in local):
   local=[]
  objects.extend(local)
  body_start=stop if stop is not None else parent['body'];chars=list(active[body_start:parent['tail']])
  for obj in local:chars[obj['start']-body_start:obj['end']-body_start]=' '*(obj['end']-obj['start'])
  remainder=_strip_layout(''.join(chars),text_wrappers=True,opaque_commands=shadowed)
  # These standard argumentless state controls bound the already-owned objects.
  remainder=re.sub(r'\\(?:selectfont|begingroup|endgroup)(?![A-Za-z@])','',remainder) if not {'begingroup','endgroup'} & shadowed else remainder
  if not local or re.sub(r'[\s{}]','',remainder):unsupported.append({'start':parent['start'],'end':parent['end'],'reason':'uncovered_compound_float_content'})
 for scope in sorted(scopes,key=lambda e:e['end']-e['start']):
  if any(e['start']<=scope['start']<e['end'] for e in floats) or any(o['start']<=scope['start']<o['end'] for o in objects):continue
  value=candidate(scope)
  if value:objects.append(value)
 # Retain the established table -> longtable compatibility boundary. Its
 # ordered rows/repeated heads are still checked by the existing table parser;
 # this only gives the same unique caption/label its stable source owner.
 for e in envs:
  if e['name']!='longtable':continue
  pos=_optional(active,e['body']);_,stop=_group(active,pos)
  value=candidate({**e,'body':stop,'kind':'longtable'}) if stop>pos else None
  if value:objects.append(value)
  else:unsupported.append({'start':e['start'],'end':e['end'],'reason':'unsupported_longtable_ownership'})
 for cap in captions:
  if not any(o['body']<=cap['start']<o['tail'] for o in objects):unsupported.append({'start':cap['start'],'end':cap['end'],'reason':cap['reason'] or 'caption_without_unique_supported_object'})
 objects.sort(key=lambda o:o['start'])
 return {'checked':True,'complete':not unsupported,'objects':objects,'unsupported':unsupported,
         'raw_container_counts':dict(Counter('figure' if 'figure' in e['name'] else 'table' for e in floats)),
         'logical_counts':dict(Counter(o['family'] for o in objects)),
         'scope_note':'Static supported source objects and coverage only; no macro expansion, semantic proof, visual inspection or automatic acceptance.'}

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
  if name in {'caption','captionof'}:
   caption=_caption_at(s,i,opaque_commands=opaque_commands)
   if caption['supported']:
    if text_wrappers:out.append(_strip_layout(caption['text'],text_wrappers=True,opaque_commands=opaque_commands))
    i=caption['end'];continue
   out.append(s[i:caption['end']]);i=caption['end'];continue
  opt=_optional(s,j)
  if name in LAYOUT_LENGTH_REGISTERS and not star:
   end=layout_assignment_end(s,j,name,opaque_commands=opaque_commands)
   if end is not None:i=end;continue
  if name in TEXT_SPACE_COMMANDS and not star:
   # These controls take no arguments: a following group remains body text.
   out.append(' ');i=j;continue
  if name in ZERO_ARG_LAYOUT_COMMANDS and not star:
   i=j;continue
  if name=='looseness' and not star:
   # Only the bounded literal paragraph adjustments used by A1 are layout.
   # Dynamic values and larger changes remain visible to preservation checks.
   value=re.match(r'\s*(?:=\s*)?([+-]?[01])(?=\s|}|$)',s[j:])
   if value:i=j+value.end();continue
  if name in FONT_DECL|RULES|{'selectfont'}:
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
  if name=='fontsize':
   end=fontsize_layout_end(s,opt) if not star else None
   if end is not None:i=end;continue
   # Preserve complete unsupported arguments rather than reparsing numbers.
   out.append(s[i:j]);i=j;continue
  if name in LAYOUT_ONE:
   _,end=_group(s,opt);i=end if end>opt else opt
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
   if env=='adjustbox' and name=='begin':
    stop=adjustbox_layout_end(s,end,opaque_commands=opaque_commands) if env not in opaque_commands else None
    if stop is not None:i=stop;continue
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
 def cell_payload(cell):
  tokens=_text_tokens(cell,opaque_commands=opaque_commands|(ID_COMMANDS-{'label'}));identifiers=_ids(cell)
  return tokens+(('cell_identifiers',identifiers),) if identifiers else tokens
 return tuple(tuple(cell_payload(cell) for cell in _split_top(row,'&'))
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
               'newcolumntype'}|DOCUMENT_COMMAND_DECLARATIONS|DOCUMENT_ENVIRONMENT_DECLARATIONS
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
   if name in DOCUMENT_COMMAND_DECLARATIONS|DOCUMENT_ENVIRONMENT_DECLARATIONS:
    argument_spec,last=_group(s,k)
    if last<=k:i=j;continue
    signature.append(argument_spec);k=last
   else:
    for _ in range(2):
     pos=k
     while pos<len(s) and s[pos].isspace():pos+=1
     if pos<len(s) and s[pos]=='[':arg,k=_group(s,pos,'[',']');signature.append(arg)
   body,end=_group(s,k)
   if end>k:
    bodies=[body]
    if name.endswith('environment') or name in DOCUMENT_ENVIRONMENT_DECLARATIONS:
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

def _payload(s:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 object_inventory=standard_layout_object_inventory(s,opaque_commands=opaque_commands);caption_records=standard_caption_declarations(s,opaque_commands=opaque_commands)
 objects=object_inventory['objects'];caption_by_start={c['start']:c for c in caption_records}
 s=active_source(s);opaque_commands=opaque_commands|_shadowed_presentation_commands(s);envs=_envs(s);floats=[e for e in envs if e['name'] in FLOAT_ENVS];tables=[e for e in envs if e['name'] in TABLE_ENVS];math=_math_spans(s,envs)
 def context(start):
  owners=[o for o in objects if o['start']<=start<o['end']]
  if len(owners)==1:
   owner=owners[0]
   return ('layout_object',owner['family'],owner['label'],tuple((g['path'],g['star']) for g in owner['graphics']))
  parents=[e for e in floats+[t for t in tables if t['name']=='longtable'] if e['start']<=start<e['end']]
  if not parents:return None
  parent=min(parents,key=lambda e:e['end']-e['start']);block=s[parent['body']:parent['tail']]
  labels=tuple(re.findall(r'\\label\s*\{([^{}]+)\}',block));graphics=tuple(re.findall(r'\\includegraphics(?:\[[^\]]*\])?\s*\{([^{}]+)\}',block))
  return (labels,graphics)
 math_body=[];math_float=[];math_locations=[]
 for m in math:
  record=('opaque_math_environment',m['name'],re.sub(r'\s+',' ',m['text']).strip()) if m.get('opaque') else (m['numbered'],_math_tokens(m['text'],opaque_commands=opaque_commands));key=context(m['start'])
  math_locations.append((m['start'],m['end'],record,_ids(m['text'])))
  if key is None:math_body.append(record)
  else:math_float.append((key,record))
 table_records=[];table_locations=[]
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
  table_locations.append((e['start'],e['end'],rows,_colspec_payload(spec,opaque_commands=opaque_commands)))
 # Preserve order *within* a uniquely bound object while permitting whole
 # objects to move. Multiplicity is retained by Counter; no global set or
 # same-container payload multiset can pay for exchanging different owners.
 def local_payload(scope,owned=(),*,retain_associations=False):
  start,end=scope['body'],scope['tail'];block=s[start:end];chars=list(block)
  excluded_owned=[(o['start'],o['end']) for o in owned]
  def covered(lo,hi):return any(a<=lo and hi<=b for a,b in excluded_owned)
  local_tables=[r for r in table_locations if ((start<=r[0] and r[1]<=end) or (scope.get('kind',scope.get('scope_kind'))=='longtable' and r[0]==scope['start'])) and not covered(r[0],r[1])]
  local_math=[r for r in math_locations if start<=r[0] and r[1]<=end and not covered(r[0],r[1])]
  local_captions=[c for c in caption_records if start<=c['start'] and c['end']<=end and not covered(c['start'],c['end'])]
  graphics=[];graphic_spans=[];pos=start
  while pos<end:
   if s[pos]!='\\':pos+=1;continue
   name,stop=_command(s,pos)
   if name!='includegraphics' or name in opaque_commands:pos=stop;continue
   star=stop<len(s) and s[stop]=='*';argument=_optional(s,stop+int(star));path,last=_group(s,argument)
   if last>argument and last<=end and not covered(pos,last):graphics.append((path,star));graphic_spans.append((pos,last))
   pos=last if last>argument else stop
  masks=excluded_owned+[(r[0],r[1]) for r in local_tables+local_math]+[(c['start'],c['end']) for c in local_captions]+graphic_spans
  for lo,hi in masks:
   lo=max(lo,start)-start;hi=min(hi,end)-start
   if hi>lo:chars[lo:hi]=' '*(hi-lo)
  residual=''.join(chars)
  if not {'begingroup','endgroup'} & opaque_commands:residual=re.sub(r'\\(?:begingroup|endgroup)(?![A-Za-z@])','',residual)
  if 'selectfont' not in opaque_commands:residual=re.sub(r'\\selectfont(?![A-Za-z@])','',residual)
  tokens=_text_tokens(residual,opaque_commands=opaque_commands|(ID_COMMANDS-{'label'}))
  ids_block=list(block)
  for lo,hi in excluded_owned:
   lo=max(lo,start)-start;hi=min(hi,end)-start
   if hi>lo:ids_block[lo:hi]=' '*(hi-lo)
  associations=[]
  if retain_associations:
   # Unowned captions must keep their order relative to labels and graphics.
   # A global caption Counter cannot establish which unsupported object owns
   # each claim. Complete supported objects are excluded as whole scopes.
   associations.extend((lo,('graphic',)+graphic) for (lo,_),graphic in zip(graphic_spans,graphics))
   for c in local_captions:
    value=('caption',)+_caption_payload(c,opaque_commands=opaque_commands) if c['supported'] else ('opaque_caption',re.sub(r'\s+',' ',c['source']).strip())
    associations.append((c['start'],value))
   for match in re.compile(r'\\label\s*\{([^{}]+)\}').finditer(s,start,end):
    if not covered(match.start(),match.end()) and not any(c['start']<=match.start()<c['end'] for c in local_captions):
     associations.append((match.start(),('label',match[1])))
  return (tuple(r[2:] for r in local_tables),tuple(r[2:] for r in local_math),tokens,
          _ids(''.join(ids_block)),tuple(graphics),tuple(value for _,value in sorted(associations,key=lambda r:r[0])))
 object_payload=[((o['family'],o['label']),local_payload(o)) for o in objects]
 unowned_payload=[]
 for e in floats:
  stop=_optional(s,e['body'])
  if e['name'] in {'wrapfigure','wraptable'}:
   for _ in range(2):_,stop=_group(s,stop)
  owned=[o for o in objects if e['start']<=o['start'] and o['end']<=e['end']]
  payload=local_payload({**e,'body':stop},owned,retain_associations=True)
  if any(payload):unowned_payload.append((context(e['start']),payload))
 # Numeric payload outside protected math/table blocks, preserving body order.
 excluded=sorted([(e['start'],e['end']) for e in tables]+[(m['start'],m['end']) for m in math])
 chars=list(s)
 for start,end in excluded:chars[start:end]=' '*(end-start)
 remainder=''.join(chars);numeric_body=[];numeric_float=[];arguments=[]
 def numbers(text):return tuple(re.findall(r'(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',_strip_layout(text,opaque_commands=opaque_commands)))
 body_parts=[];cursor=0
 regions=sorted(floats+[o for o in objects if not any(e['start']<=o['start']<e['end'] for e in floats)],key=lambda e:e['start'])
 for e in regions:
  if e['start']<cursor:continue
  body_parts.append(remainder[cursor:e['start']]);local=[o for o in objects if e['start']<=o['start'] and o['end']<=e['end']];position=e['start']
  for owner in local:
   gap=numbers(remainder[position:owner['start']])
   if gap:numeric_float.append((('unowned_float_gap',context(e['start']),position-e['start']),gap))
   values=numbers(remainder[owner['start']:owner['end']])
   if values:numeric_float.append((context(owner['start']),values))
   position=owner['end']
  values=numbers(remainder[position:e['end']])
  if values:numeric_float.append((context(e['start']) if not local else ('unowned_float_gap',context(e['start']),position-e['start']),values))
  cursor=e['end']
 body_parts.append(remainder[cursor:]);numeric_body=numbers(''.join(body_parts))
 i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if i in caption_by_start:
   caption=caption_by_start[i]
   if caption['supported']:
    arguments.append((context(i),'caption')+_caption_payload(caption,opaque_commands=opaque_commands))
   else:arguments.append((context(i),'opaque_caption',re.sub(r'\s+',' ',caption['source']).strip()))
   i=caption['end'];continue
  if name in PROTECTED_ARGS:
   j=_optional(s,j);arg,end=_group(s,j)
   if end>j:arguments.append((context(i),name,_text_tokens(arg,opaque_commands=opaque_commands)));i=end;continue
  i=j
 return {'configuration_arguments':_configuration_payload(s,opaque_commands=opaque_commands),'macro_definitions':_macro_definitions(s),'shadowed_literal_url_invocations':_shadowed_literal_url_payload(s),'math_body':tuple(math_body),'math_floats':tuple(sorted(Counter(math_float).items(),key=repr)),'tables':tuple(sorted(Counter(table_records).items(),key=repr)),'identifiers':_ids(s),'numbers_body':numeric_body,'numbers_floats':tuple(sorted(Counter(numeric_float).items(),key=repr)),'protected_arguments':tuple(sorted(Counter(arguments).items(),key=repr)),'layout_objects':tuple(sorted(Counter(object_payload).items(),key=repr)),'caption_label_bindings':tuple(sorted(Counter(((o['family'],o['label']),_caption_binding_payload(o['label_binding'])) for o in objects).items(),key=repr)),'unowned_float_payload':tuple(sorted(Counter(unowned_payload).items(),key=repr))}

_OBJECT_PRESENTATION_COMMANDS=FONT_DECL|RULES|LAYOUT_ONE|LAYOUT_LENGTH_REGISTERS|TEXT_SPACE_COMMANDS|TEXT_WRAPPERS|ID_COMMANDS|PROTECTED_ARGS|DELIMITER_SIZE_COMMANDS|DELIMITER_COMMANDS|{
 'begin','end','setlength','addtolength','selectfont','relax','begingroup','endgroup',
 'resizebox','scalebox','rotatebox','raisebox','multirow','multicolumn','shortstack',
 'checkmark','toprule','midrule','bottomrule','hline','cline','cmidrule','addlinespace',
 'pm','mp','times','cdot','frac','dfrac','tfrac','sqrt','sum','prod','int','iint','lim',
 'log','ln','exp','sin','cos','tan','min','max','arg','det','mod','bmod','pmod',
 'mathbb','mathbf','mathcal','mathrm','mathsf','mathtt','mathit','boldsymbol',
 'operatorname','text','textrm','mbox','notag','nonumber','tag','le','ge','leq','geq',
 'neq','ne','equiv','approx','sim','simeq','in','notin','subset','subseteq','supset',
 'supseteq','to','rightarrow','leftarrow','leftrightarrow','Rightarrow','Leftarrow',
 'Leftrightarrow','infty','partial','nabla','ell','ldots','cdots','dots','vdots','ddots',
 'alpha','beta','gamma','delta','epsilon','varepsilon','zeta','eta','theta','vartheta',
 'iota','kappa','lambda','mu','nu','xi','pi','rho','sigma','tau','upsilon','phi',
 'varphi','chi','psi','omega','Gamma','Delta','Theta','Lambda','Xi','Pi','Sigma',
 'Upsilon','Phi','Psi','Omega','hat','bar','tilde','vec','dot','ddot','overline',
 'underbrace','overbrace','%','&','_','#','$','{','}','\\',',',';','!',' ',
}


def _identified_layout_objects(source:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[List[Dict[str,Any]],List[str]]:
 """Bind only unique, self-contained math/table objects, without macro expansion."""
 s=active_source(source);envs=_envs(s);math=_math_spans(s,envs)
 from source_map import masked_tex
 executable=masked_tex(source)
 floats=[e for e in envs if e['name'] in FLOAT_ENVS]
 tables=[e for e in envs if e['name'] in TABLE_ENVS]
 all_labels=re.findall(r'\\label\s*\{([^{}]+)\}',s);objects=[];reasons=[]
 if re.search(r'\\(?:if[A-Za-z@]*|else|fi|csname|catcode|input|include)(?![A-Za-z@])',s):
  return [],['conditional_dynamic_or_external_source']
 caption_records=standard_caption_declarations(source,opaque_commands=opaque_commands)
 declarations=_macro_definitions(s)
 defined={str(r[1] if r[0] in {'def','gdef','edef','xdef','let'} else r[2]).lstrip('\\') for r in declarations}
 opaque=opaque_commands|_shadowed_presentation_commands(s)
 for kind,item in [('table',e) for e in tables]+[('math',m) for m in math]:
  start,end=item['start'],item['end'];parent=None
  parents=[e for e in floats if e['start']<=start and end<=e['end']]
  if parents:
   parent=min(parents,key=lambda e:e['end']-e['start'])
   # A table cannot become a Figure merely by retaining its label/cells.
   if kind=='table' and not parent['name'].removesuffix('*').endswith('table'):continue
   if kind=='math' and not parent['name'].removesuffix('*').endswith('figure'):continue
   children=[e for e in tables if parent['body']<=e['start'] and e['end']<=parent['tail']]
   if kind=='table' and len(children)!=1:continue
   if kind=='math' and (children or len([m for m in math if parent['body']<=m['start'] and m['end']<=parent['tail']])!=1):continue
   start,end=parent['start'],parent['end']
  elif kind=='table':
   # A bare included table fragment may place its caption/label immediately
   # before tabular. No ordinary text/unknown invocation can enter this prefix.
   captions=[c for c in caption_records if c['command']=='caption' and c['supported'] and c['end']<=start]
   if captions:
    prefix_start=captions[-1]['start']
    prefix=s[prefix_start:start]
    if not _strip_layout(prefix,opaque_commands=opaque).strip(' \t\r\n{}'):
     start=prefix_start
  if start>=len(executable) or executable[start]!='\\':continue
  # Unsupported readers/macros directly before the object may consume its
  # first token. Known no-argument presentation controls remain independent.
  prefix=re.search(r'\\([A-Za-z@]+)\s*$',s[:start])
  if prefix and prefix[1] not in FONT_DECL|RULES|{'selectfont','relax','begingroup','endgroup','maketitle'}:
   reasons.append('adjacent_unknown_object_reader');continue
  block=s[start:end]
  known_envs=FLOAT_ENVS|TABLE_ENVS|MATH_WRAPPERS|{'adjustbox','center'}
  if any(start<=e['start'] and e['end']<=end and
         (e['name'] not in known_envs or e['name'].removesuffix('*') in defined) for e in envs):
   reasons.append('unknown_inner_environment');continue
  labels=re.findall(r'\\label\s*\{([^{}]+)\}',block)
  if len(labels)!=1 or all_labels.count(labels[0])!=1:continue
  label=labels[0]
  if any(e['start']<=start and end<=e['end'] and e['name'] not in FLOAT_ENVS|TABLE_ENVS|MATH_WRAPPERS|{'document','adjustbox','center'} for e in envs):
   reasons.append('unknown_outer_environment:'+label);continue
  commands=set(re.findall(r'\\([A-Za-z@]+|.)',block,re.S))
  if commands&defined or commands-(_OBJECT_PRESENTATION_COMMANDS):
   reasons.append('unknown_or_defined_object_command:'+label);continue
  if commands&opaque:
   reasons.append('shadowed_object_command:'+label);continue
  local_captions=[c for c in caption_records if start<=c['start'] and c['end']<=end]
  if len(local_captions)>1 or any(not c['supported'] for c in local_captions):
   reasons.append('unsupported_caption_binding:'+label);continue
  caption_data=tuple(_caption_payload(c,opaque_commands=opaque,
                     family='table' if kind=='table' and c['family'] is None else c['family']) for c in local_captions)
  local_tables=[e for e in tables if start<=e['start'] and e['end']<=end]
  local_math=[m for m in math if start<=m['start'] and m['end']<=end and not any(e['start']<=m['start']<e['end'] for e in local_tables)]
  if any(m.get('opaque') for m in local_math):continue
  # Full ordered cell/formula payload remains attached to this exact label.
  table_data=[]
  for e in local_tables:
   body=s[e['body']:e['tail']];offset=_optional(body,0);spec=''
   for _ in range(2 if e['name'] in {'tabular*','tabularx'} else 1):spec,offset=_group(body,offset)
   body=re.sub(r'\\(?:cmidrule|cline)(?:\[[^\]]*\])?\{[^{}]*\}','',body[offset:])
   body=re.sub(r'\\(?:toprule|midrule|bottomrule|hline)(?:\[[^\]]*\])?','',body)
   table_data.append((_table_rows(body,opaque_commands=opaque),_colspec_payload(spec,opaque_commands=opaque)))
  math_data=tuple((m['numbered'],_math_tokens(m['text'],opaque_commands=opaque)) for m in local_math)
  chars=list(block)
  for e in local_tables+local_math:
   lo,hi=e['start']-start,e['end']-start;chars[lo:hi]=' '*(hi-lo)
  extra=_text_tokens(''.join(chars),opaque_commands=opaque|(ID_COMMANDS-{'label'}))
  label_start=start+re.search(r'\\label\s*\{([^{}]+)\}',block).start()
  binding_data=tuple(_caption_binding_payload(_caption_label_binding(s,c,label_start,start,end,opaque_commands=opaque)) for c in local_captions)
  payload=(kind,tuple(table_data),math_data,extra,_ids(block),caption_data,binding_data)
  objects.append({'label':label,'kind':kind,'start':start,'end':end,
                  'scope':parent['name'] if parent else 'body','payload':payload})
 # Nested or overlapping bindings are not independent objects.
 objects.sort(key=lambda o:o['start'])
 if any(a['end']>b['start'] for a,b in zip(objects,objects[1:])):
  return [],reasons+['overlapping_object_bindings']
 return objects,reasons


def _opaque_source_invocations(source:str)->Tuple[str,...]:
 """Retain outside unknown invocations verbatim, including enclosing arguments.

 Unchanged outside macros do not poison a self-contained object. Moving an
 object out of an unknown argument changes this record and cannot be proved.
 """
 from source_map import masked_tex
 s=active_source(source);executable=masked_tex(source)
 known=_OBJECT_PRESENTATION_COMMANDS|{'documentclass','usepackage','RequirePackage',
     'bibliography','bibliographystyle','maketitle','newcommand','renewcommand',
     'providecommand','DeclareRobustCommand','newenvironment','renewenvironment',
     'provideenvironment','def','gdef','edef','xdef','let'}
 records=[];index=0
 while index<len(s):
  if s[index]!='\\' or executable[index]!='\\':index+=1;continue
  name,end=_command(s,index)
  if name in known:index=end;continue
  stop=end
  if stop<len(s) and s[stop]=='*':stop+=1
  while True:
   pos=stop
   while pos<len(s) and s[pos].isspace():pos+=1
   if pos>=len(s) or s[pos] not in '[{':break
   opening=s[pos];_,last=_group(s,pos,opening,']' if opening=='[' else '}')
   if last==pos:break
   stop=last
  records.append(s[index:stop])
  index=stop
 return tuple(records)


def identified_layout_equivalence(original:str,repaired:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Exact per-label payload proof for finite same-file wrapper migrations."""
 before,old_reasons=_identified_layout_objects(original,opaque_commands=opaque_commands)
 after,new_reasons=_identified_layout_objects(repaired,opaque_commands=opaque_commands)
 a={o['label']:o for o in before};b={o['label']:o for o in after}
 result={'checked':True,'status':'not_proven','objects':[],
         'reasons':old_reasons+new_reasons,
         'limits':'Unique same-file objects and exact ordered token payload only; no macro/conditional/include expansion, algebraic equivalence or PDF legibility proof.'}
 if not a or set(a)!=set(b):return result
 if _opaque_source_invocations(original)!=_opaque_source_invocations(repaired):
  result['reasons'].append('outside_opaque_invocation_changed');return result
 if any(a[label]['kind']!=b[label]['kind'] or a[label]['payload']!=b[label]['payload'] for label in a):return result
 changed=[label for label in a if a[label]['scope']!=b[label]['scope']]
 if not changed:return result
 def residual(source,objects):
  active=active_source(source)
  for obj in reversed(objects):active=active[:obj['start']]+' '*(obj['end']-obj['start'])+active[obj['end']:]
  return _payload(active,opaque_commands=opaque_commands)
 # Residual body order, unbound floats, declarations, identifiers and numbers
 # must all agree. Neither numeric multisets nor unmatched objects are waived.
 if residual(original,before)!=residual(repaired,after):return result
 if result['reasons']:return result
 result['status']='proven'
 for label in sorted(a):
  payload=a[label]['payload']
  result['objects'].append({'label':label,'kind':a[label]['kind'],
    'before_scope':a[label]['scope'],'after_scope':b[label]['scope'],
    'payload_sha256':hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode('utf-8')).hexdigest()})
 return result


def _finite_length_uncertainty(original:str,repaired:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Explain opaque-adjacent finite length differences without certifying them.

 A reader can consume a following register/control as data. Masking is only
 used to classify an otherwise exact numeric-only difference as pending review;
 it never establishes preservation or authorizes a candidate.
 """
 def masked(source):
  active=active_source(source)
  opaque=opaque_commands|_shadowed_presentation_commands(active)
  chars=list(active);records=[];i=0
  while i<len(active):
   if active[i]!='\\':i+=1;continue
   name,end=_command(active,i);stop=None
   if name in {'setlength','addtolength'} and name not in opaque:
    stop=layout_length_end(active,end,opaque_commands=opaque)
   elif name in LAYOUT_LENGTH_REGISTERS:
    stop=layout_assignment_end(active,end,name,opaque_commands=opaque)
   if stop is not None:
    records.append({'command':name,'source_start':i,'source_end':stop,
                    'spelling':active[i:stop]})
    chars[i:stop]=' '*(stop-i);i=stop
   else:i=end
  return ''.join(chars),records
 old,old_records=masked(original);new,new_records=masked(repaired)
 exact=bool(old_records or new_records) and _payload(old,opaque_commands=opaque_commands)==_payload(new,opaque_commands=opaque_commands)
 return {'checked':True,'status':'pending' if exact else 'not_applicable',
         'before_operations':old_records if exact else [],
         'after_operations':new_records if exact else [],
         'reason':'Finite standard length syntax accounts for the numeric difference, but adjacent opaque command arity or expansion is unproved.' if exact else None,
         'limits':'Classification only. Raw scientific failure remains; inspect actual declarations, preserve content and review the full current PDF.'}



def _reference_anchor_uncertainty(original:str,repaired:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Bounded missing-anchor additions and existing counter-order changes.

 Labels do not supply scientific content, but their location/numbering needs an
 actual source and PDF review. This never certifies an insertion automatically.
 """
 from source_map import masked_tex
 old=active_source(original);new=active_source(repaired)
 definitions=_macro_definitions(old)+_macro_definitions(new)
 defined={str(r[1] if r[0] in {'def','gdef','edef','xdef','let'} else r[2]).lstrip('\\') for r in definitions}
 if defined&{'label','ref','pageref','eqref','autoref','cref','Cref'}:
  return {'checked':True,'status':'not_applicable'}
 if (opaque_commands|_shadowed_presentation_commands(old)|_shadowed_presentation_commands(new))&{'label','caption','captionof','stepcounter','refstepcounter','setcounter','addtocounter'}:
  return {'checked':True,'status':'not_applicable'}
 if _macro_definitions(old)!=_macro_definitions(new):
  return {'checked':True,'status':'not_applicable'}
 pattern=re.compile(r'\\label\s*\{([^{}]+)\}')
 def labels(s,source):
  executable=masked_tex(source)
  return [m for m in pattern.finditer(s) if executable[m.start()]=='\\']
 before=labels(old,original);after=labels(new,repaired)
 old_counts=Counter(m[1] for m in before);new_counts=Counter(m[1] for m in after)
 if old_counts==new_counts:
  a={(o['family'],o['label']):o for o in standard_layout_object_inventory(original,opaque_commands=opaque_commands)['objects']}
  b={(o['family'],o['label']):o for o in standard_layout_object_inventory(repaired,opaque_commands=opaque_commands)['objects']}
  changed=[key for key in a if key in b and _caption_binding_payload(a[key]['label_binding'])!=_caption_binding_payload(b[key]['label_binding'])]
  if set(a)==set(b) and changed and all(old_counts[key[1]]==1 for key in changed):
   affected={key[1] for key in changed}
   def without_labels(s,records):
    chars=list(s)
    for match in records:
     if match[1] in affected:chars[match.start():match.end()]=' '*(match.end()-match.start())
    return ''.join(chars)
   old_rest=without_labels(old,before);new_rest=without_labels(new,after)
   try:
    from layout_prose_integrity import compare_layout_prose
   except ImportError:
    from .layout_prose_integrity import compare_layout_prose
   if _payload(old_rest,opaque_commands=opaque_commands)==_payload(new_rest,opaque_commands=opaque_commands) and compare_layout_prose(old_rest,new_rest,opaque_commands=opaque_commands).get('status')=='pass':
    return {'checked':True,'status':'pending','added_anchors':[],
            'relocated_anchors':[{'family':key[0],'label':key[1],'before_binding':a[key]['label_binding'],'after_binding':b[key]['label_binding']} for key in changed],
            'reason':'Only existing unique anchors changed literal order relative to their caption/counter controls; all remaining protected scientific and prose payload agrees.',
            'limits':'Raw scientific failure remains. Either direction may correct or damage a reference; explicit host source/current-PDF intended-target review is required. No TeX counters were executed.'}
 if any(new_counts[key]!=count for key,count in old_counts.items()):
  return {'checked':True,'status':'not_applicable'}
 added=set(new_counts)-set(old_counts)
 refs={key for (family,key),count in _ids(old) if family=='ref' and count>0}
 if not added or not added<=refs or any(new_counts[key]!=1 for key in added):
  return {'checked':True,'status':'not_applicable'}
 chars=list(new);records=[]
 for match in after:
  if match[1] in added:
   chars[match.start():match.end()]=' '*(match.end()-match.start())
   records.append({'label':match[1],'source_start':match.start(),'source_end':match.end()})
 residual=''.join(chars)
 if _payload(old,opaque_commands=opaque_commands)!=_payload(residual,opaque_commands=opaque_commands):
  return {'checked':True,'status':'not_applicable'}
 try:
  from layout_prose_integrity import compare_layout_prose
 except ImportError:
  from .layout_prose_integrity import compare_layout_prose
 if compare_layout_prose(old,residual,opaque_commands=opaque_commands).get('status')!='pass':
  return {'checked':True,'status':'not_applicable'}
 return {'checked':True,'status':'pending','added_anchors':records,
         'reason':'Only unique missing anchors already referenced in the original active source were added; all remaining protected payload is identical.',
         'limits':'Raw scientific failure remains. The host must check the intended object, numbering, current references, and all fresh PDF pages; this does not authorize a candidate.'}


def reference_font_hooks(source:str,*,opaque_commands:frozenset[str]=frozenset())->List[Dict[str,Any]]:
 """Finite preamble-only, single-command bibliography font hooks.

 This does not expand includes or certify template compliance. Shared spelling
 recognition lets policy and content diagnostics use the same narrow boundary.
 """
 active=active_source(source)
 from source_map import masked_tex
 executable=masked_tex(source)
 declarations=_macro_definitions(active)
 defined={str(r[1] if r[0] in {'def','gdef','edef','xdef','let'} else r[2]).lstrip('\\') for r in declarations}
 if (defined|opaque_commands)&{'AddToHook','thebibliography','small','footnotesize','normalsize'}:return []
 document=re.search(r'\\begin\s*\{document\}',active)
 if document is None:return []
 records=[];pattern=re.compile(r'\\AddToHook\s*\{(?:env/thebibliography/begin|cmd/thebibliography/after)\}\s*\{\s*\\(small|footnotesize|normalsize)\s*\}')
 for match in pattern.finditer(active,0,document.start()):
  if executable[match.start()]!='\\':continue
  records.append({'start':match.start(),'end':match.end(),'source':active[match.start():match.end()],'font_command':match[1]})
 return records

def reference_font_hook_uncertainty(original:str,repaired:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 old_records=reference_font_hooks(original,opaque_commands=opaque_commands);new_records=reference_font_hooks(repaired,opaque_commands=opaque_commands)
 if [r['source'] for r in old_records]==[r['source'] for r in new_records]:
  return {'checked':True,'status':'not_applicable'}
 def residual(source,records):
  chars=list(active_source(source))
  for record in records:chars[record['start']:record['end']]=' '*(record['end']-record['start'])
  return ''.join(chars)
 old=residual(original,old_records);new=residual(repaired,new_records)
 try:
  from layout_prose_integrity import compare_layout_prose
 except ImportError:
  from .layout_prose_integrity import compare_layout_prose
 if _payload(old,opaque_commands=opaque_commands)!=_payload(new,opaque_commands=opaque_commands) or compare_layout_prose(old,new,opaque_commands=opaque_commands).get('status')!='pass':
  return {'checked':True,'status':'not_applicable'}
 return {'checked':True,'status':'pending','before_hooks':old_records,'after_hooks':new_records,
         'reason':'Only a finite bibliography-local font hook changed; residual scientific and prose payload is identical.',
         'limits':'Template compatibility and full-PDF references/body typography require explicit host review; other hooks, extra code and redefined hook/font commands are not covered.'}


def combined_layout_uncertainty(original:str,repaired:str,*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Bounded composition of reviewable presentation differences, never proof.

 Mask only finite standard lengths, unique already-referenced missing labels,
 and finite bibliography hooks. All residual scientific and ordered prose
 payload must agree. This does not waive changes by numeric multiset equality.
 """
 def residual(source):
  active=active_source(source);opaque=opaque_commands|_shadowed_presentation_commands(active)
  chars=list(active);lengths=[];i=0
  while i<len(active):
   if active[i]!='\\':i+=1;continue
   name,end=_command(active,i);stop=None
   if name in {'setlength','addtolength'} and name not in opaque:
    stop=layout_length_end(active,end,opaque_commands=opaque)
   elif name in LAYOUT_LENGTH_REGISTERS:
    stop=layout_assignment_end(active,end,name,opaque_commands=opaque)
   if stop is not None:
    lengths.append({'command':name,'source_start':i,'source_end':stop,'spelling':active[i:stop]})
    chars[i:stop]=' '*(stop-i);i=stop
   else:i=end
  hooks=reference_font_hooks(source,opaque_commands=opaque_commands)
  for hook in hooks:chars[hook['start']:hook['end']]=' '*(hook['end']-hook['start'])
  return active,chars,lengths,hooks
 old,old_chars,old_lengths,old_hooks=residual(original)
 new,new_chars,new_lengths,new_hooks=residual(repaired)
 definitions=_macro_definitions(old)+_macro_definitions(new)
 defined={str(r[1] if r[0] in {'def','gdef','edef','xdef','let'} else r[2]).lstrip('\\') for r in definitions}
 if (defined|opaque_commands)&{'label','ref','pageref','eqref','autoref','cref','Cref'}:
  return {'checked':True,'status':'not_applicable'}
 from source_map import masked_tex
 def labels(active,source):
  executable=masked_tex(source)
  return [m for m in re.finditer(r'\\label\s*\{([^{}]+)\}',active) if executable[m.start()]=='\\']
 old_labels=labels(old,original);new_labels=labels(new,repaired)
 a=Counter(m[1] for m in old_labels);b=Counter(m[1] for m in new_labels)
 if any(b[key]!=count for key,count in a.items()):return {'checked':True,'status':'not_applicable'}
 added=set(b)-set(a);refs={key for (family,key),count in _ids(old) if family=='ref' and count>0}
 if not added<=refs or any(b[key]!=1 for key in added):return {'checked':True,'status':'not_applicable'}
 anchors=[]
 for match in new_labels:
  if match[1] in added:
   new_chars[match.start():match.end()]=' '*(match.end()-match.start())
   anchors.append({'label':match[1],'source_start':match.start(),'source_end':match.end()})
 families=[]
 if [r['spelling'] for r in old_lengths]!=[r['spelling'] for r in new_lengths]:families.append('layout_argument_uncertainty')
 if added:families.append('reference_anchor_uncertainty')
 if [r['source'] for r in old_hooks]!=[r['source'] for r in new_hooks]:families.append('reference_font_hook_uncertainty')
 if len(families)<2:return {'checked':True,'status':'not_applicable'}
 old_rest=''.join(old_chars);new_rest=''.join(new_chars)
 try:
  from layout_prose_integrity import compare_layout_prose
 except ImportError:
  from .layout_prose_integrity import compare_layout_prose
 if _payload(old_rest,opaque_commands=opaque_commands)!=_payload(new_rest,opaque_commands=opaque_commands) or compare_layout_prose(old_rest,new_rest,opaque_commands=opaque_commands).get('status')!='pass':
  return {'checked':True,'status':'not_applicable'}
 return {'checked':True,'status':'pending','families':families,
         'before_lengths':old_lengths,'after_lengths':new_lengths,'added_anchors':anchors,
         'before_hooks':old_hooks,'after_hooks':new_hooks,
         'reason':'Only the enumerated presentation/anchor families account for the combined differences; every residual scientific and ordered prose payload agrees.',
         'limits':'Classification only. Original raw changes remain. Requires actual declaration evidence where needed and full current source/template/PDF review.'}

def _document_presentation_uncertainty(original:str,repaired:str,*,opaque_commands:frozenset[str],dynamic_context_commands:frozenset[str])->Dict[str,Any]:
 """Retain finite edits obscured solely by a constructed template context.

 No equivalence or approval follows from this classification. Literal macro
 shadows are excluded by the caller; every residual protected token must agree.
 """
 if not dynamic_context_commands:return {'checked':False,'status':'not_applicable'}
 from source_map import masked_tex
 fonts={'tiny','scriptsize','footnotesize','small','normalsize','selectfont'}
 def residual(source):
  active=active_source(source);executable=masked_tex(source);chars=list(active);records=[];i=0
  while i<len(active):
   if active[i]!='\\' or executable[i]!='\\':i+=1;continue
   name,end=_command(active,i);stop=None
   if name not in dynamic_context_commands:i=end;continue
   star=end<len(active) and active[end]=='*'
   if name in {'hspace','vspace'}:
    argument,last=_group(active,end+int(star))
    if last>end+int(star) and _literal_dimension(argument):stop=last
   elif name in fonts and not star:stop=end
   elif name=='fontsize' and not star:
    last=fontsize_layout_end(active,end)
    if last is not None:
     size,pos=_group(active,end);baseline,_=_group(active,pos)
     if float(size.strip().removesuffix('pt').strip())>0 and float(baseline.strip().removesuffix('pt').strip())>0:stop=last
   if stop is not None:
    records.append({'command':name,'source_start':i,'source_end':stop,'spelling':active[i:stop]})
    chars[i:stop]=' '*(stop-i);i=stop
   else:i=end
  return ''.join(chars),records
 old,old_records=residual(original);new,new_records=residual(repaired)
 if [r['spelling'] for r in old_records]==[r['spelling'] for r in new_records]:return {'checked':True,'status':'not_applicable'}
 from layout_prose_integrity import compare_layout_prose
 if _payload(old,opaque_commands=opaque_commands)!=_payload(new,opaque_commands=opaque_commands) or compare_layout_prose(old,new,opaque_commands=opaque_commands).get('status')!='pass':
  return {'checked':True,'status':'not_applicable'}
 return {'checked':True,'status':'pending','before_operations':old_records,'after_operations':new_records,
         'reason':'Only enumerated finite presentation spellings differ, but imported constructed TeX prevents their standard semantics from being proved.',
         'limits':'Candidate retention only. Raw scientific/prose failures remain, literal shadows are excluded, and no host assertion or this classifier can establish an automated pass.'}


def _presentation_macro_uncertainty(original:str,repaired:str,before:Dict[str,Any],after:Dict[str,Any],*,opaque_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 """Finite layout-only body edits in otherwise identical macro declarations.

 This is a review trigger, not macro expansion or a preservation proof. Opaque
 math, aliases, changed signatures and changed literal content do not qualify.
 """
 result={'checked':True,'status':'not_applicable'}
 old=before['macro_definitions'];new=after['macro_definitions']
 if len(old)!=len(new):return result
 opaque=opaque_commands|_shadowed_presentation_commands(active_source(original))|_shadowed_presentation_commands(active_source(repaired))
 changed=[]
 for left,right in zip(old,new):
  if left==right:continue
  if (len(left)!=len(right) or left[:-1]!=right[:-1]
      or left[0] in REGISTER_DECLARATIONS|{'let','DeclareMathOperator'}):return result
  bodies_left=left[-1] if isinstance(left[-1],tuple) else (left[-1],)
  bodies_right=right[-1] if isinstance(right[-1],tuple) else (right[-1],)
  if len(bodies_left)!=len(bodies_right):return result
  for a,b in zip(bodies_left,bodies_right):
   # Macro bodies are not checked by file-local presentation policy. Keep
   # graphics/scaling/font/hiding operations literal, so zero scale, draft,
   # phantom and font changes cannot be mistaken for harmless uncertainty.
   # Initially this route only relaxes finite literal hspace/vspace bodies;
   # unsupported spacing forms and shadowed commands remain literal too.
   commands=frozenset(re.findall(r'\\([A-Za-z@]+|[^A-Za-z])',a+'\n'+b))
   body_opaque=opaque|(commands-{'hspace','vspace'})
   if _strip_layout(a,opaque_commands=body_opaque)!=_strip_layout(b,opaque_commands=body_opaque):return result
  changed.append(str(left[1] if left[0] in {'def','gdef','edef','xdef'} else left[2]))
 if changed:
  result.update(status='pending',declarations=changed,
                reason='Declaration identities/signatures and all residual literal payload agree; only finite presentation syntax differs. Current source and PDF still need host review.')
 return result


def scientific_host_review_report(report:Dict[str,Any])->Dict[str,Any]:
 """Retain a diagnosed uncertainty without certifying preservation.

 The token checker keeps its raw failed result. Only a completed assessment
 that explicitly has no confirmed changed component can become pending host
 review at a mutation boundary. Missing assessments and confirmed changes do
 not qualify, and no host assertion can turn this report into a pass.
 """
 assessment=report.get('assessment')
 if (report.get('checked') is True and report.get('status')=='failed'
     and isinstance(assessment,dict)
     and assessment.get('classification')=='manual_review'
     and assessment.get('confirmed_changed_components')==[]
     and any(report.get(name,{}).get('status')=='pending' for name in ('presentation_macro_uncertainty','document_presentation_uncertainty'))):
  return {**report,'automated_report':report,'automated_status':report['status'],
          'status':'manual_review','verification_method':'pending_host_review',
          'review_note':'Candidate may be retained for source and current-PDF review; automated preservation remains unproved. This does not authorize content changes or final acceptance.'}
 return report


def scientific_payload_diff(original:str,repaired:str,*,_template_glue_review:bool=True,opaque_commands:frozenset[str]=frozenset(),dynamic_context_commands:frozenset[str]=frozenset())->Dict[str,Any]:
 limits='Conservative token invariants, not mathematical equivalence; No macro expansion or mathematical equivalence proof; exotic TeX definitions/restructuring require host review. Cross-file checking requires callers to compare every modified source file.'
 def summary(value):
  return {'sha256':hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest(),'component_counts':{k:len(v) for k,v in value.items()}}
 try:
  before=_payload(original,opaque_commands=opaque_commands);after=_payload(repaired,opaque_commands=opaque_commands);changed=[key for key in before if before[key]!=after[key]]
  presentation_macro=_presentation_macro_uncertainty(original,repaired,before,after,opaque_commands=opaque_commands) if changed==['macro_definitions'] else {'checked':False,'status':'not_applicable'}
  before_summary=summary(before);after_summary=summary(after)
  raw_changed=list(changed)
  equivalence=identified_layout_equivalence(original,repaired,opaque_commands=opaque_commands) if original!=repaired else {'checked':True,'status':'not_needed','objects':[],'reasons':[]}
  if equivalence.get('status')=='proven':changed=[]
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
 # Ownership changes can alter every locally bound channel when a missing
 # label is restored, and opaque finite lengths also enter object residuals.
 # These are diagnostic trigger sets only. Each classifier still masks its
 # exact bounded operations and requires *all* residual payload to agree;
 # changed claims/cells/math/assets cannot become a preservation result.
 object_channels={'math_floats','tables','numbers_floats','protected_arguments','layout_objects','caption_label_bindings','unowned_float_payload'}
 uncertainty=_finite_length_uncertainty(original,repaired,opaque_commands=opaque_commands) if changed and set(changed)<={'numbers_body','numbers_floats','layout_objects','unowned_float_payload'} else {'checked':False,'status':'not_applicable'}
 anchors=_reference_anchor_uncertainty(original,repaired,opaque_commands=opaque_commands) if changed and set(changed)<={'identifiers'}|object_channels else {'checked':False,'status':'not_applicable'}
 font_hook=reference_font_hook_uncertainty(original,repaired,opaque_commands=opaque_commands)
 combined=combined_layout_uncertainty(original,repaired,opaque_commands=opaque_commands) if changed and set(changed)<={'numbers_body','identifiers'}|object_channels else {'checked':False,'status':'not_applicable'}
 document_presentation=_document_presentation_uncertainty(original,repaired,opaque_commands=opaque_commands,dynamic_context_commands=dynamic_context_commands) if changed else {'checked':False,'status':'not_applicable'}
 template_glue={'checked':False,'status':'not_applicable'}
 if _template_glue_review and changed and set(changed)<={'macro_definitions','numbers_body','numbers_floats','protected_arguments','layout_objects','unowned_float_payload'}:
  try:
   from template_parameter_review import classify_template_glue_override
  except ImportError:
   from .template_parameter_review import classify_template_glue_override
  template_glue=classify_template_glue_override(original,repaired)
 review=[]
 for key in changed:
  if uncertainty.get('status')=='pending' or anchors.get('status')=='pending' or font_hook.get('status')=='pending' or combined.get('status')=='pending' or template_glue.get('status')=='pending' or document_presentation.get('status')=='pending':
   review.append(key);continue
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
         'raw_comparison':{'status':'failed' if raw_changed else 'pass','changed_components':raw_changed,
                           'regression_reasons':['Scientific payload changed: '+k for k in raw_changed],
                           'before':before_summary,'after':after_summary},
         'layout_equivalence':equivalence,
         'layout_argument_uncertainty':uncertainty,
         'reference_anchor_uncertainty':anchors,
         'reference_font_hook_uncertainty':font_hook,
         'combined_layout_uncertainty':combined,
         **({'presentation_macro_uncertainty':presentation_macro} if presentation_macro.get('status')=='pending' else {}),
         **({'document_presentation_uncertainty':document_presentation} if document_presentation.get('status')=='pending' else {}),
         **({'template_parameter_uncertainty':template_glue} if template_glue.get('status')=='pending' else {}),
         'assessment':{'classification':classification,'scope':'protected_token_invariants',
                       'semantic_damage_proven':False,'requires_host_review':bool(changed),
                       'confirmed_changed_components':confirmed,'review_changed_components':review},
         'limits':limits}
