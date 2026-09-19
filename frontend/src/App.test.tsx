// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import App from './App';
import { api, PortalApiError } from './lib/api';
import type { Study } from './lib/types';

vi.mock('./lib/api', async importOriginal => {
 const real = await importOriginal<typeof import('./lib/api')>();
 return { ...real, api: { bootstrap: vi.fn(), listStudies: vi.fn(), getStudy: vi.fn(), saveStudy: vi.fn(), searchProjects: vi.fn(), start: vi.fn(), recover: vi.fn(), exportCsv: vi.fn(), logout: vi.fn() } };
});
const fixture: Study = {
 name:'BOM-1', title:'Class 10 sample', company:'Demo', currency:'EUR', standard_description:'Standard Class 10', mode:'Historical only', status:'Needs Review', modified:'v1',
 projects:[{project:'P1',vehicle_count:1}], materials:[{name:'M1',description:'<img src=x onerror=alert(1)>',stock_uom:'Nos',proposed_quantity:2,unit_cost:100,cost_known:1,review_status:'Pending',assembly:'Suspension',purpose:'Standard',amount:200,notes:'Evidence',evidence:'[{"invoice":"demo"}]'}], labor:[],
 material_allowance:0,overhead_allowance:0,target_margin:30,material_total:200,estimated_total:200,suggested_retail:0
};
beforeEach(() => {
 vi.clearAllMocks();
 vi.mocked(api.bootstrap).mockResolvedValue({user:'demo',companies:[{name:'Demo',currency:'EUR'}],assemblies:[],purposes:[]});
 vi.mocked(api.listStudies).mockResolvedValue([{name:'BOM-1',title:'Class 10 sample',status:'Needs Review'}]);
 vi.mocked(api.getStudy).mockResolvedValue(structuredClone(fixture));
 vi.mocked(api.searchProjects).mockResolvedValue([]);
});
afterEach(cleanup);
async function openStudy() {
 const user=userEvent.setup(); render(<App/>);
 await user.click(await screen.findByRole('button',{name:/Class 10 sample/}));
 await screen.findByLabelText('Quantity for Component 1');
 return user;
}
describe('standalone review workflow',()=>{
 it('protects unsaved edits when switching and saves a valid review payload',async()=>{
  const user=await openStudy();
  const quantity=screen.getByLabelText('Quantity for Component 1');
  await user.clear(quantity); await user.type(quantity,'4');
  expect(screen.getByRole('button',{name:'Export CSV'})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'New study'}));
  expect(screen.getByRole('dialog')).toHaveTextContent('Discard unsaved changes?');
  await user.click(screen.getByRole('button',{name:'Keep editing'}));
  expect(quantity).toHaveValue(4);
  vi.mocked(api.saveStudy).mockResolvedValue({...fixture,modified:'v2',materials:[{...fixture.materials[0],proposed_quantity:4,amount:400}]});
  await user.click(screen.getByRole('button',{name:'Save changes'}));
  await waitFor(()=>expect(api.saveStudy).toHaveBeenCalledOnce());
  const sent=vi.mocked(api.saveStudy).mock.calls[0][0];
  expect(sent.projects).toEqual([{project:'P1',vehicle_count:1}]);
  expect(sent.modified).toBe('v1');
  expect(sent.materials[0]).toMatchObject({name:'M1',proposed_quantity:4,stock_uom:'Nos'});
  expect(sent.materials[0]).not.toHaveProperty('evidence');
  await waitFor(()=>expect(screen.getByRole('button',{name:'Export CSV'})).toBeEnabled());
 });
 it('disables export while a different study is loading',async()=>{
  vi.mocked(api.listStudies).mockResolvedValue([{name:'BOM-1',title:'Class 10 sample',status:'Needs Review'},{name:'BOM-2',title:'Second study',status:'Needs Review'}]);
  const user=await openStudy();
  vi.mocked(api.getStudy).mockImplementationOnce(()=>new Promise(()=>{}));
  await user.click(screen.getByRole('button',{name:/Second study/}));
  expect(screen.getByRole('button',{name:'Export CSV'})).toBeDisabled();
 });
 it('marks an entered positive cost as known',async()=>{
  vi.mocked(api.getStudy).mockResolvedValue({...fixture,materials:[{...fixture.materials[0],unit_cost:0,cost_known:0}]});
  const user=await openStudy();
  await user.type(screen.getByLabelText('Unit cost for Component 1'),'25');
  expect(screen.getByLabelText('Cost known for Component 1')).toBeChecked();
 });
 it('uses company currency, escapes source text, and does not offer rebuilding a reviewed study',async()=>{
  await openStudy();
  expect(screen.queryByRole('button',{name:'Build draft'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('Description for Component 1')).toHaveValue('<img src=x onerror=alert(1)>');
  expect(document.querySelector('img')).toBeNull();
  expect(screen.getAllByText(/€200/).length).toBeGreaterThan(0);
 });
 it('keeps manual review rows disabled before a draft exists',async()=>{
  const user=userEvent.setup(); render(<App/>);
  await user.click((await screen.findAllByRole('button',{name:'New study'}))[0]);
  expect(screen.getByRole('button',{name:'Add manual material'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Build draft'})).toBeEnabled();
 });
 it('shows a sign-in destination and hides the workspace when API access is denied',async()=>{
  vi.mocked(api.bootstrap).mockRejectedValue(new PortalApiError('Denied','auth',403));
  render(<App/>);
  expect(await screen.findByRole('link',{name:/Return to sign in/})).toHaveAttribute('href','/login?redirect-to=%2Fvehicle-bom');
  expect(screen.queryByText('Class 10 sample')).not.toBeInTheDocument();
 });
});
