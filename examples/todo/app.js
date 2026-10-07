let todos=[];
const list=document.querySelector('#tasks');
function render(){
  list.replaceChildren();
  document.querySelector('#count').textContent=`${todos.filter(t=>!t.done).length} remaining`;
  document.querySelector('#empty').hidden=todos.length!==0;
  for(const todo of todos){
    const li=document.createElement('li');li.className=todo.done?'done':'';
    const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.checked=todo.done;
    const title=document.createElement('span');title.textContent=todo.title;label.append(input,title);li.append(label);list.append(li);
    input.addEventListener('change',()=>save({id:todo.id}));
  }
}
async function save(body){
  try{const response=await fetch('/api/todos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw Error('Could not save. Please try again.');todos=await response.json();document.querySelector('#error').textContent='';render();return true;}
  catch(error){document.querySelector('#error').textContent=error.message;render();return false;}
}
document.querySelector('#add-form').addEventListener('submit',async event=>{event.preventDefault();const input=document.querySelector('#new-task');if(await save({title:input.value}))input.value='';});
try{const response=await fetch('/api/todos');if(!response.ok)throw Error('Could not load tasks.');todos=await response.json();render();}catch(error){document.querySelector('#error').textContent=error.message;}
