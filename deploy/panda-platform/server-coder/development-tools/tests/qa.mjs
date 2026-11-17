import assert from 'node:assert/strict';
export default async function({browser,getTestAccount}) {
 const a=await getTestAccount();
 for(const viewport of [{width:1280,height:800},{width:390,height:844}]) {
  const page=await browser.newPage({viewport});
  await page.setContent('<h1>Login fixture</h1><label>Email<input aria-label="Email"></label><label>Password<input aria-label="Password" type="password"></label>');
  await page.getByLabel('Email').fill(a.email);await page.getByLabel('Password').fill(a.password);
  assert.equal(await page.getByLabel('Email').inputValue(),a.email);
  assert.equal(await page.getByLabel('Password').inputValue(),a.password);
  assert.ok((await page.locator('h1').boundingBox()).height>20);await page.close();
 }
}
