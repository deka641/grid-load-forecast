describe('test setup', () => {
  it('blocks network access through fetch', async () => {
    await expect(fetch('https://www.smard.de')).rejects.toThrow('network access is disabled')
  })
})
