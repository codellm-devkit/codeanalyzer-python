class Environment:
    def __getitem__(self, name):
        return Model()


class Model:
    env = Environment()

    def search(self, domain):
        return []

    def helper(self):
        return 42


class AccountMove(Model):
    def action_post(self):
        accounts = self.env['account.account'].search([])
        self.helper()
        return str(len(accounts))
